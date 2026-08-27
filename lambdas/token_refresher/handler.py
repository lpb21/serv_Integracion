"""
Lambda: token-refresher
Trigger: EventBridge Scheduler cada 50 minutos

Obtiene el Bearer token de Swift OAuth2 y lo guarda
cifrado en DynamoDB con KMS via swift_store.

Environment variables:
  SWIFT_ENV            sandbox | test | prod
  DYNAMODB_TABLE_NAME  Tabla DynamoDB del store
  KMS_KEY_ID           ID o ARN de la clave KMS
"""

import json
import logging
import os

from shared.swift_store import get_credentials, save_token
from shared.swift_auth import fetch_swift_token

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ENV = os.environ.get('SWIFT_ENV', 'sandbox')


def lambda_handler(event, context):
    logger.info(f'token-refresher: starting (env={ENV})')

    # 1. Leer credenciales desde DynamoDB (descifradas con KMS)
    credentials = get_credentials()

    # 2. Obtener Bearer token de Swift
    token_data = fetch_swift_token(credentials)
    logger.info(f"token-refresher: token obtained, expires_in={token_data.get('expires_in')}")

    # 3. Guardar token cifrado en DynamoDB con KMS
    save_token(token_data)

    logger.info('token-refresher: completed successfully')
    return {
        'statusCode': 200,
        'body': json.dumps({'message': 'Token refreshed and saved to DynamoDB'}),
    }


if __name__ == '__main__':
    # Ejecución local: python -m lambdas.token_refresher.handler
    result = lambda_handler({}, None)
    print(json.dumps(result, indent=2))
