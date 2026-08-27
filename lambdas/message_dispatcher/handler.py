"""
Lambda: message-dispatcher
Invocado async por message-poller (InvocationType=Event)

Responsabilidades:
  1. Lee Bearer token de DynamoDB
  2. Descarga el payload de la distribucion (link rel:download)
  3. Decodifica base64 -> texto plano (MT o XML MX)
  4. Publica en la cola FIFO de entrada del core
  5. Envia ACK a Swift - remueve la distribucion de la lista pendiente

Enrutamiento:
  MT (fin) y MX (interAct) entran por UNA sola cola FIFO:
    SQS_CORE_INBOUND_URL
  Al publicar en la cola FIFO van MessageGroupId (por servicio) y
  MessageDeduplicationId (distribution.id), que arma el queue_router.

Nota sobre errores:
  - Fallo antes del ACK: NO se hace NAK (NAK remueve permanentemente).
    Se omite el ACK y la distribucion reaparece en el siguiente polling.
  - El async retry config del Lambda reintenta 2 veces, luego DLQ.

Environment variables:
  SWIFT_ENV             sandbox | test | prod
  DYNAMODB_TABLE_NAME   Tabla DynamoDB del store
  KMS_KEY_ID            ID de la clave KMS
  SQS_CORE_INBOUND_URL  Cola FIFO de entrada del core (MT y MX juntos)
  SQS_DLQ_URL           DLQ del conector
"""

import json
import logging
import os
from datetime import datetime, timezone
 
import boto3
 
from shared.swift_store import get_token, get_credentials
from shared.swift_client_outbound import send_messages, detect_service, SwiftApiError
from shared.mt_parser import parse_mt_message
from shared.mx_parser import parse_mx_message, detect_message_format
 
logger = logging.getLogger()
logger.setLevel(logging.INFO)
 
_sqs_client = None
 
FATAL_KEYWORDS = ('not found', 'required', 'exceeds', 'Cannot detect', 'missing', 'empty')
 
 
def _get_sqs_client():
    global _sqs_client
    if _sqs_client is None:
        _sqs_client = boto3.client('sqs')
    return _sqs_client
 
 
def _now_iso():
    return datetime.now(timezone.utc).isoformat()
 
 
def transform_message(raw_body: str) -> dict:
    """Transforma el body SQS (texto plano MT o MX) al formato del spec."""
    if not raw_body or not raw_body.strip():
        raise ValueError('SQS body is empty')
 
    fmt = detect_message_format(raw_body)
 
    if fmt == 'MT':
        swift_message = parse_mt_message(raw_body)
    elif fmt == 'MX':
        swift_message = parse_mx_message(raw_body)
    else:
        raise ValueError('Cannot determine message format from body content')
 
    meta = swift_message.pop('_meta', {})
 
    return {
        'swift_message': swift_message,
        'format':        fmt,
        'service':       detect_service(swift_message['message_type']),
        'meta':          meta,
    }
 
 
def publish_result(sqs_message_id, swift_message, swift_response, service, fmt):
    # La cola de resultados es FIFO, asi que van MessageGroupId y
    # MessageDeduplicationId.
    #   - MessageGroupId: POR SERVICIO (fin / interAct), coherente con la misma
    #     decision tomada en la cola de entrada (queue_router). Los resultados de
    #     MT se ordenan entre si y los de MX entre si, pero pueden ir en paralelo.
    #   - MessageDeduplicationId: sender_reference + estado ("-Accepted"). Se
    #     incluye el estado para que un resultado Accepted y uno Failed del mismo
    #     mensaje (si se reintentara) no se cancelen entre si en la ventana FIFO.
    sender_reference = swift_message['sender_reference']
    _get_sqs_client().send_message(
        QueueUrl=os.environ['SQS_SEND_RESULTS_URL'],
        MessageBody=json.dumps({
            'sender_reference':        sender_reference,
            'message_cloud_reference': swift_response.get('message_cloud_reference'),
            'message_type':            swift_message['message_type'],
            'service':                 service,
            'format':                  fmt,
            'status':                  'Accepted',
            'sentAt':                  _now_iso(),
            'sqsMessageId':            sqs_message_id,
            'swiftEnv':                os.environ.get('SWIFT_ENV', 'sandbox'),
        }),
        MessageAttributes={
            'status':          {'DataType': 'String', 'StringValue': 'Accepted'},
            'service':         {'DataType': 'String', 'StringValue': service},
            'format':          {'DataType': 'String', 'StringValue': fmt},
            'senderReference': {'DataType': 'String', 'StringValue': sender_reference},
            'messageType':     {'DataType': 'String', 'StringValue': swift_message['message_type']},
        },
        MessageGroupId=service,
        MessageDeduplicationId=f'{sender_reference}-Accepted',
    )
 
 
def publish_error(sqs_message_id, error, swift_message=None, service=None):
    error_code = getattr(error, 'swift_code', None) or 'PARSE_ERROR'
    # La cola de resultados es FIFO, asi que van MessageGroupId y
    # MessageDeduplicationId.
    #   - MessageGroupId: por servicio (coherente con la decision general). Si el
    #     error ocurrio antes de poder detectar el servicio (p.ej. fallo el
    #     parseo), no hay service; se usa "errors" como grupo de reserva.
    #   - MessageDeduplicationId: sqsMessageId + "-Failed". Se usa el sqsMessageId
    #     (siempre unico) en vez del sender_reference, porque ante un fallo de
    #     parseo el sender_reference puede ser 'unknown' y colisionaria entre
    #     mensajes distintos.
    group_id = service or 'errors'
    try:
        _get_sqs_client().send_message(
            QueueUrl=os.environ['SQS_SEND_RESULTS_URL'],
            MessageBody=json.dumps({
                'sender_reference': (swift_message or {}).get('sender_reference', 'unknown'),
                'message_type':     (swift_message or {}).get('message_type', 'unknown'),
                'status':           'Failed',
                'error_code':       error_code,
                'error_text':       str(error)[:500],
                'failedAt':         _now_iso(),
                'sqsMessageId':     sqs_message_id,
                'swiftEnv':         os.environ.get('SWIFT_ENV', 'sandbox'),
            }),
            MessageAttributes={
                'status':    {'DataType': 'String', 'StringValue': 'Failed'},
                'errorCode': {'DataType': 'String', 'StringValue': error_code},
            },
            MessageGroupId=group_id,
            MessageDeduplicationId=f'{sqs_message_id}-Failed',
        )
    except Exception as e:
        logger.error(f'Failed to publish error result: {e}')
 
 
def is_fatal(error) -> bool:
    """Errores fatales se descartan; transientes se reintentan."""
    status = getattr(error, 'status_code', None)
    if status and 400 <= status < 500:
        return True
    msg = str(error)
    return any(kw in msg for kw in FATAL_KEYWORDS)
 
 
def lambda_handler(event, context):
    batch_item_failures = []
 
    # Cargar token y credenciales PKI (con cache en memoria)
    token_data  = get_token()
    token       = token_data['access_token']
    creds       = get_credentials()
 
    credentials = {
        'private_key_pem': creds['private_key_pem'],
        'certificate_pem': creds['certificate_pem'],
    }
 
    for record in event['Records']:
        transformed = None
 
        try:
            raw_body = record['body']
 
            # 1. Detectar formato y parsear MT/MX -> formato del spec
            transformed = transform_message(raw_body)
            swift_message = transformed['swift_message']
            fmt           = transformed['format']
            service       = transformed['service']
            meta          = transformed['meta']
 
            logger.info(json.dumps({
                'msg':              'message-sender: parsed',
                'format':           fmt,
                'service':          service,
                'message_type':     swift_message['message_type'],
                'sender_reference': swift_message['sender_reference'],
                'receiver':         swift_message.get('receiver'),
                'uetr':             meta.get('uetr'),
                'sqsMessageId':     record['messageId'],
            }))
 
            # 2. Enviar a Swift con X-SWIFT-Signature
            swift_response = send_messages(token, swift_message, credentials)
 
            logger.info(json.dumps({
                'msg':                     'message-sender: sent',
                'sender_reference':        swift_message['sender_reference'],
                'message_cloud_reference': swift_response.get('message_cloud_reference'),
            }))
 
            # 3. Notificar resultado al core
            publish_result(record['messageId'], swift_message, swift_response, service, fmt)
 
        except Exception as err:
            logger.error(json.dumps({
                'msg':          'message-sender: error',
                'sqsMessageId': record['messageId'],
                'error':        str(err),
                'swiftCode':    getattr(err, 'swift_code', None),
                'statusCode':   getattr(err, 'status_code', None),
            }))
 
            publish_error(
                record['messageId'],
                err,
                transformed['swift_message'] if transformed else None,
                # El service solo existe si el parseo alcanzo a detectarlo;
                # si fallo antes, va None y publish_error usa "errors".
                transformed['service'] if transformed else None,
            )
 
            # Solo reintentar errores transientes
            if not is_fatal(err):
                batch_item_failures.append({'itemIdentifier': record['messageId']})
 
    return {'batchItemFailures': batch_item_failures}
 
 
if __name__ == '__main__':
    import sys
    event = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {'Records': []}
    result = lambda_handler(event, None)
    print(json.dumps(result, indent=2))