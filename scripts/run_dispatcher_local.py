"""
scripts/run_dispatcher_local.py
Prueba el flujo poller -> dispatcher contra el sandbox real de Swift:
lista distribuciones, procesa la primera y verifica las colas del core.

Requiere token previo:
  python -m lambdas.token_refresher.handler

Uso:
  export $(grep -v "^#" .env.local | xargs)
  python scripts/run_dispatcher_local.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import boto3  # noqa: E402
from shared.swift_store import get_token  # noqa: E402
from shared.swift_client import list_distributions  # noqa: E402
from lambdas.message_dispatcher.handler import lambda_handler  # noqa: E402

print('\n=== message-dispatcher local test ===\n')

# 1. Obtener token
token = get_token()['access_token']
print('[1/3] Token obtenido OK\n')

# 2. Listar distribuciones del sandbox
distributions = list_distributions(token)
print(f'[2/3] Distribuciones disponibles: {len(distributions)}')

if not distributions:
    print('      No hay distribuciones en el sandbox.')
    sys.exit(0)

for i, d in enumerate(distributions[:5]):
    print(f"      [{i + 1}] id={d['id']} service={d['service']} type={d['type']}")

# 3. Procesar la primera
first = distributions[0]
print(f"\n[3/3] Procesando distribucion {first['id']} ({first['service']}/{first['type']})...")

try:
    lambda_handler({
        'distributionId':        first['id'],
        'service':               first['service'],
        'type':                  first['type'],
        'links':                 first.get('links', []),
        'messageCloudReference': first.get('message_cloud_reference'),
    }, None)
    print('      Dispatcher completado OK')
except Exception as e:
    print(f'      Dispatcher error: {e}')

# 4. Ver que llego a las colas del core
print('\n--- Mensajes en colas del core ---')
sqs = boto3.client('sqs', endpoint_url=os.environ.get('AWS_ENDPOINT_URL'))

for name, url_env in [('MT inbound', 'SQS_CORE_MT_INBOUND_URL'),
                      ('MX inbound', 'SQS_CORE_MX_INBOUND_URL')]:
    url = os.environ.get(url_env)
    if not url:
        continue
    try:
        response = sqs.receive_message(
            QueueUrl=url,
            MaxNumberOfMessages=5,
            WaitTimeSeconds=1,
            MessageAttributeNames=['All'],
        )
        messages = response.get('Messages', [])
        if messages:
            print(f'\n{name} ({len(messages)} mensaje(s)):')
            for m in messages:
                attrs = m.get('MessageAttributes', {})
                svc   = attrs.get('service', {}).get('StringValue')
                dtype = attrs.get('distributionType', {}).get('StringValue')
                print(f"  service={svc} type={dtype} body[0..80]={m['Body'][:80]}...")
    except Exception as e:
        print(f'{name}: error {e}')
