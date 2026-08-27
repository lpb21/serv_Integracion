"""
scripts/setup_credentials.py
Carga las credenciales Swift en DynamoDB con cifrado KMS.
Ejecutar UNA VEZ en el deploy inicial o cuando rotan las credenciales.

Uso:
  export $(cat .env.sandbox | xargs)
  python scripts/setup_credentials.py

Variables requeridas:
  SWIFT_ENV=sandbox
  SWIFT_CONSUMER_KEY=...
  SWIFT_CONSUMER_SECRET=...
  DYNAMODB_TABLE_NAME=swift-sqs-sandbox-store
  KMS_KEY_ID=...

Para prod tambien (llaves institucionales):
  SWIFT_PRIVATE_KEY_FILE=/path/key.pem
  SWIFT_CERTIFICATE_FILE=/path/cert.pem
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from shared.swift_store import save_credentials  # noqa: E402

ENV = os.environ.get('SWIFT_ENV', 'sandbox')

print(f'\nSetup credentials -> DynamoDB ({ENV})\n')

consumer_key    = os.environ.get('SWIFT_CONSUMER_KEY')
consumer_secret = os.environ.get('SWIFT_CONSUMER_SECRET')

if not consumer_key:
    raise SystemExit('ERROR: SWIFT_CONSUMER_KEY not set')
if not consumer_secret:
    raise SystemExit('ERROR: SWIFT_CONSUMER_SECRET not set')

# En sandbox las llaves RSA las provee Swift dinamicamente -
# se guardan placeholders que swift_auth sobreescribe en runtime.
private_key_pem = os.environ.get('SWIFT_PRIVATE_KEY_PEM', 'SANDBOX_PLACEHOLDER')
certificate_pem = os.environ.get('SWIFT_CERTIFICATE_PEM', 'SANDBOX_PLACEHOLDER')

if os.environ.get('SWIFT_PRIVATE_KEY_FILE'):
    with open(os.environ['SWIFT_PRIVATE_KEY_FILE']) as f:
        private_key_pem = f.read().strip()

if os.environ.get('SWIFT_CERTIFICATE_FILE'):
    with open(os.environ['SWIFT_CERTIFICATE_FILE']) as f:
        certificate_pem = f.read().strip()

print(f'Consumer key: {consumer_key}')
print(f'Environment:  {ENV}')
print(f"Table:        {os.environ.get('DYNAMODB_TABLE_NAME')}")
print(f"KMS Key:      {(os.environ.get('KMS_KEY_ID') or '')[:30]}...")
print('')

save_credentials({
    'consumer_key':    consumer_key,
    'consumer_secret': consumer_secret,
    'private_key_pem': private_key_pem,
    'certificate_pem': certificate_pem,
})

print('OK - Credentials saved to DynamoDB with KMS encryption')
print('')
print('Next step - invoke token-refresher to get the first Bearer token:')
print(f'  aws lambda invoke --function-name swift-sqs-{ENV}-token-refresher /dev/null')
