"""
Lambda: message-sender
Trigger: SQS - cola outbound del core bancario

El core publica mensajes MT o MX como TEXTO PLANO en el body SQS
- sin JSON wrapper, sin metadata adicional.

Responsabilidades:
  1. Lee token y credenciales PKI de DynamoDB
  2. Auto-detecta formato MT vs MX por el contenido del body
  3. Parsea el mensaje y extrae los campos del spec de Swift
  4. Construye X-SWIFT-Signature (JWS RS256 con digest SHA256)
  5. POST a Swift (/fin/messages o /interact/messages)
  6. Publica resultado en SQS send-results para que el core correlacione

Errores fatales (no reintentar): parseo MT/MX, 4xx de Swift.
Errores transientes (reintentar via batchItemFailures): 5xx, red.

Environment variables:
  SWIFT_ENV             sandbox | test | prod
  DYNAMODB_TABLE_NAME   Tabla DynamoDB del store
  KMS_KEY_ID            ID de la clave KMS
  SQS_SEND_RESULTS_URL  Cola de resultados hacia el core
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

FATAL_KEYWORDS = ('not found', 'required', 'exceeds', 'cannot determine', 'cannot detect', 'missing', 'empty', 'deserialize', 'key data')


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
    _get_sqs_client().send_message(
        QueueUrl=os.environ['SQS_SEND_RESULTS_URL'],
        MessageBody=json.dumps({
            'sender_reference':        swift_message['sender_reference'],
            'message_cloud_reference': swift_response.get('message_cloud_reference'),
            'message_type':            swift_message['message_type'],
            'service':                 service,
            'format':                  fmt,
            'status':                  'Accepted',
            'sentAt':                  _now_iso(),
            'sqsMessageId':            sqs_message_id,
            'swiftEnv':                os.environ.get('SWIFT_ENV', 'sandbox'),
        }),
        MessageGroupId=service or 'results',
        MessageDeduplicationId=f"{swift_message['sender_reference']}-Accepted",
        MessageAttributes={
            'status':          {'DataType': 'String', 'StringValue': 'Accepted'},
            'service':         {'DataType': 'String', 'StringValue': service},
            'format':          {'DataType': 'String', 'StringValue': fmt},
            'senderReference': {'DataType': 'String', 'StringValue': swift_message['sender_reference']},
            'messageType':     {'DataType': 'String', 'StringValue': swift_message['message_type']},
        },
    )


def publish_error(sqs_message_id, error, swift_message=None):
    error_code = getattr(error, 'swift_code', None) or 'PARSE_ERROR'
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
            MessageGroupId='errors',
            MessageDeduplicationId=f'{sqs_message_id}-Failed',
            MessageAttributes={
                'status':    {'DataType': 'String', 'StringValue': 'Failed'},
                'errorCode': {'DataType': 'String', 'StringValue': error_code},
            },
        )
    except Exception as e:
        logger.error(f'Failed to publish error result: {e}')


def is_fatal(error) -> bool:
    """Errores fatales se descartan; transientes se reintentan."""
    status = getattr(error, 'status_code', None)
    if status and 400 <= status < 500:
        return True
    msg = str(error).lower()
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
