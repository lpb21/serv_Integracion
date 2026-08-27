"""
scripts/setup_local.py
Levanta LocalStack y crea todos los recursos AWS locales.

Prerequisitos:
  - Docker Desktop corriendo con WSL2 integration
  - Imagen: localstack/localstack:3.8.0

Uso: python scripts/setup_local.py
"""

import json
import subprocess
import time

ENV    = 'sandbox'
REGION = 'us-east-1'
ACCT   = '000000000000'  # LocalStack default

AWSLOCAL_ENV = {
    'AWS_ACCESS_KEY_ID':     'test',
    'AWS_SECRET_ACCESS_KEY': 'test',
    'AWS_DEFAULT_REGION':    REGION,
}


def run(cmd, ignore_error=False, env_extra=None):
    import os
    env = {**os.environ, **(env_extra or {})}
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, env=env)
    if result.returncode != 0 and not ignore_error:
        raise RuntimeError(f'Command failed: {cmd}\n{result.stderr}')
    return result.stdout.strip()


print('\n=== Swift SQS Connector - Setup Local (LocalStack) ===\n')

# 1. Levantar LocalStack si no esta corriendo
print('[1/4] Verificando LocalStack...')
running = run('docker ps --filter name=localstack --format "{{.Names}}"', ignore_error=True)

if 'localstack' not in (running or ''):
    print('      Levantando LocalStack 3.8.0...')
    run('docker run --rm -d --name localstack -p 4566:4566 '
        '-v /var/run/docker.sock:/var/run/docker.sock localstack/localstack:3.8.0')
    ready = False
    for _ in range(30):
        time.sleep(1)
        health = run('curl -s http://localhost:4566/_localstack/health', ignore_error=True)
        if health and '"sqs"' in health:
            ready = True
            break
    if not ready:
        raise SystemExit('LocalStack no respondio en 30 segundos')
    print('      LocalStack listo OK')
else:
    print('      LocalStack ya esta corriendo OK')

# 2. Crear colas SQS
print('\n[2/4] Creando colas SQS...')
queues = [
    'core-swift-mt-inbound',
    'core-swift-mx-inbound',
    'core-swift-outbound',
    f'swift-sqs-{ENV}-dlq',
    f'swift-sqs-{ENV}-send-results',
]

for q in queues:
    run(f'awslocal sqs create-queue --queue-name {q} --region {REGION}',
        ignore_error=True, env_extra=AWSLOCAL_ENV)
    print(f'      {q} OK')

# 3. Crear KMS key y tabla DynamoDB
print('\n[3/4] Creando KMS key y tabla DynamoDB...')
kms_key_id = run(
    f'awslocal kms create-key --region {REGION} --query KeyMetadata.KeyId --output text',
    env_extra=AWSLOCAL_ENV)
run(f'awslocal kms create-alias --alias-name alias/swift-sqs-{ENV}-store '
    f'--target-key-id {kms_key_id} --region {REGION}',
    ignore_error=True, env_extra=AWSLOCAL_ENV)
print(f'      KMS key: {kms_key_id} OK')

run(f'awslocal dynamodb create-table '
    f'--table-name swift-sqs-{ENV}-store '
    f'--attribute-definitions AttributeName=pk,AttributeType=S '
    f'--key-schema AttributeName=pk,KeyType=HASH '
    f'--billing-mode PAY_PER_REQUEST '
    f'--region {REGION}',
    ignore_error=True, env_extra=AWSLOCAL_ENV)
print(f'      DynamoDB table: swift-sqs-{ENV}-store OK')

# 4. Generar .env.local
print('\n[4/4] Generando .env.local...')

env_content = f"""# Generado por scripts/setup_local.py - NO editar manualmente
SWIFT_ENV=sandbox

# LocalStack
AWS_ENDPOINT_URL=http://localhost:4566
AWS_REGION={REGION}
AWS_DEFAULT_REGION={REGION}
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test

# DynamoDB + KMS
DYNAMODB_TABLE_NAME=swift-sqs-{ENV}-store
KMS_KEY_ID={kms_key_id}

# Colas del core (LocalStack)
SQS_CORE_MT_INBOUND_URL=http://localhost:4566/{ACCT}/core-swift-mt-inbound
SQS_CORE_MX_INBOUND_URL=http://localhost:4566/{ACCT}/core-swift-mx-inbound
SQS_CORE_FILEACT_URL=
SQS_SEND_RESULTS_URL=http://localhost:4566/{ACCT}/swift-sqs-{ENV}-send-results
SQS_DLQ_URL=http://localhost:4566/{ACCT}/swift-sqs-{ENV}-dlq

# Lambda
DISPATCHER_FUNCTION_NAME=swift-sqs-{ENV}-message-dispatcher
MAX_CONCURRENT_DISPATCHERS=5
FILEACT_CALLBACK_URL=
"""

with open('.env.local', 'w') as f:
    f.write(env_content)
print('      .env.local OK')

print('\n=======================================================')
print('  Setup completo OK')
print('=======================================================\n')
print('Siguiente paso - cargar credenciales Swift:')
print('')
print('  export $(grep -v "^#" .env.local | xargs)')
print('  export SWIFT_CONSUMER_KEY=<tu_key>')
print('  export SWIFT_CONSUMER_SECRET=<tu_secret>')
print('  python scripts/setup_credentials.py')
print('')
print('Luego prueba el token:')
print('  python -m lambdas.token_refresher.handler')
