"""
Lambda: message-poller
Trigger: EventBridge Scheduler - rate(2 minutes)

Responsabilidades:
  1. Lee Bearer token de DynamoDB (descifrado con KMS via swift_store)
  2. GET /distributions - paginado con rel:next
  3. Invoca message-dispatcher async (InvocationType=Event) por cada distribucion

El poller NO descarga payloads - solo descubre IDs y delega al dispatcher.

Environment variables:
  SWIFT_ENV                   sandbox | test | prod
  DYNAMODB_TABLE_NAME         Tabla DynamoDB del store
  KMS_KEY_ID                  ID de la clave KMS
  DISPATCHER_FUNCTION_NAME    Nombre o ARN del message-dispatcher
  MAX_CONCURRENT_DISPATCHERS  Maximo de dispatchers en paralelo (default 25)
"""

import json
import logging
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3

from shared.swift_store import get_token
from shared.swift_client import list_distributions

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_lambda_client = None

MAX_CONCURRENT = int(os.environ.get('MAX_CONCURRENT_DISPATCHERS', '25'))


def _get_lambda_client():
    global _lambda_client
    if _lambda_client is None:
        _lambda_client = boto3.client('lambda')
    return _lambda_client


def invoke_dispatcher(distribution: dict) -> None:
    """Invoca el dispatcher de forma asincrona (fire-and-forget)."""
    payload = {
        'distributionId':        distribution['id'],
        'service':               distribution['service'],
        'type':                  distribution['type'],
        'copy':                  distribution.get('copy', False),
        'messageCloudReference': distribution.get('message_cloud_reference'),
        'links':                 distribution.get('links', []),
    }

    _get_lambda_client().invoke(
        FunctionName=os.environ['DISPATCHER_FUNCTION_NAME'],
        InvocationType='Event',
        Payload=json.dumps(payload).encode('utf-8'),
    )


def dispatch_all(distributions: list) -> tuple:
    """Despacha distribuciones en paralelo respetando MAX_CONCURRENT."""
    dispatched = 0
    failed     = 0

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as executor:
        futures = {executor.submit(invoke_dispatcher, d): d for d in distributions}

        for future in as_completed(futures):
            try:
                future.result()
                dispatched += 1
            except Exception as e:
                failed += 1
                dist = futures[future]
                logger.error(f"Failed to invoke dispatcher for {dist['id']}: {e}")

    return dispatched, failed


def lambda_handler(event, context):
    logger.info('message-poller: starting')

    # 1. Leer token de DynamoDB (con cache en memoria)
    token_data = get_token()
    token = token_data['access_token']

    # 2. GET /distributions - paginado
    distributions = list_distributions(token)
    logger.info(f'message-poller: found {len(distributions)} distribution(s)')

    if not distributions:
        return {'statusCode': 200, 'message': 'No distributions available'}

    # Log por tipo
    summary = Counter(f"{d['service']}/{d['type']}" for d in distributions)
    logger.info(f'message-poller: breakdown {dict(summary)}')

    # 3. Invocar dispatcher async por cada distribucion
    dispatched, failed = dispatch_all(distributions)
    logger.info(f'message-poller: dispatched={dispatched} failed={failed}')

    return {'statusCode': 200, 'dispatched': dispatched, 'failed': failed}


if __name__ == '__main__':
    result = lambda_handler({}, None)
    print(json.dumps(result, indent=2))
