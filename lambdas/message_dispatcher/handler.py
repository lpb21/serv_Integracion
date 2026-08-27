"""
Lambda: message-dispatcher
Invocado async por message-poller (InvocationType=Event)

Responsabilidades:
  1. Lee Bearer token de DynamoDB
  2. Descarga el payload de la distribucion usando el link rel:download
  3. Decodifica base64 -> texto plano (MT o XML MX)
  4. Publica en la cola SQS del core correspondiente
  5. Envia ACK a Swift - remueve la distribucion de la lista pendiente

Enrutamiento:
  fin      -> SQS_CORE_MT_INBOUND_URL
  interAct -> SQS_CORE_MX_INBOUND_URL
  fileAct  -> SQS_CORE_FILEACT_URL (vacio = error descriptivo)

Nota sobre errores:
  - Fallo antes del ACK: NO se hace NAK (NAK remueve permanentemente).
    Se omite el ACK y la distribucion reaparece en el siguiente polling.
  - El async retry config del Lambda reintenta 2 veces, luego DLQ.

Environment variables:
  SWIFT_ENV               sandbox | test | prod
  DYNAMODB_TABLE_NAME     Tabla DynamoDB del store
  KMS_KEY_ID              ID de la clave KMS
  SQS_CORE_MT_INBOUND_URL Cola MT del core
  SQS_CORE_MX_INBOUND_URL Cola MX del core
  SQS_CORE_FILEACT_URL    Cola FileAct del core (opcional)
  SQS_DLQ_URL             DLQ del conector
  FILEACT_CALLBACK_URL    Callback URL para FileAct async (opcional)
"""

import json
import logging

import boto3

from shared.swift_store import get_token
from shared.swift_client import download_distribution, ack_distribution
from shared.queue_router import resolve_queue_url, build_sqs_message

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_sqs_client = None


def _get_sqs_client():
    global _sqs_client
    if _sqs_client is None:
        _sqs_client = boto3.client('sqs')
    return _sqs_client


def lambda_handler(event, context):
    distribution_id = event['distributionId']
    service         = event['service']
    dist_type       = event['type']
    links           = event.get('links', [])

    logger.info(f'message-dispatcher: processing id={distribution_id} '
                f'service={service} type={dist_type}')

    # 1. Leer token de DynamoDB
    token_data = get_token()
    token = token_data['access_token']

    # 2. Descargar distribucion de Swift (usa link rel:download)
    downloaded = download_distribution(token, {
        'id':      distribution_id,
        'service': service,
        'type':    dist_type,
        'links':   links,
    })

    # 3. Resolver cola del core y construir mensaje SQS
    queue_url = resolve_queue_url(service)
    sqs_msg   = build_sqs_message(downloaded)

    # 4. Publicar en cola del core (texto plano)
    result = _get_sqs_client().send_message(
        QueueUrl=queue_url,
        MessageBody=sqs_msg['message_body'],
        MessageAttributes=sqs_msg['message_attributes'],
    )

    logger.info(f'message-dispatcher: published to SQS '
                f'id={distribution_id} messageId={result["MessageId"]}')

    # 5. ACK a Swift - DESPUES de publicar exitosamente en SQS.
    #    Si algo falla antes de este punto, NO se hace ACK ni NAK:
    #    la distribucion reaparece en el siguiente polling (retry natural).
    ack_distribution(token, distribution_id)

    return {'statusCode': 200, 'distributionId': distribution_id}


if __name__ == '__main__':
    import sys
    event = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    result = lambda_handler(event, None)
    print(json.dumps(result, indent=2))
