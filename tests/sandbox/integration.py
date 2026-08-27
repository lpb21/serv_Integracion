"""
tests/sandbox/integration.py
Test de integracion contra el sandbox real de Swift:
  1. Token OAuth2
  2. GET /distributions
  3. Descarga de la primera distribucion

Requiere .env.sandbox con SWIFT_CONSUMER_KEY / SWIFT_CONSUMER_SECRET.

Uso:
  export $(grep -v "^#" .env.sandbox | xargs)
  python tests/sandbox/integration.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from shared.swift_auth import fetch_swift_token  # noqa: E402
from shared.swift_client import list_distributions, download_distribution  # noqa: E402

print('\n=== Integracion sandbox Swift ===\n')

creds = {
    'consumer_key':    os.environ['SWIFT_CONSUMER_KEY'],
    'consumer_secret': os.environ['SWIFT_CONSUMER_SECRET'],
    'private_key_pem': 'SANDBOX_PLACEHOLDER',
    'certificate_pem': 'SANDBOX_PLACEHOLDER',
}

# 1. Token
print('[1/3] Obteniendo token OAuth2...')
token_data = fetch_swift_token(creds)
token = token_data['access_token']
print(f"      Token OK (expires_in={token_data['expires_in']})")

# 2. Distribuciones
print('\n[2/3] GET /distributions...')
distributions = list_distributions(token)
print(f'      {len(distributions)} distribucion(es)')
for i, d in enumerate(distributions[:5]):
    print(f"      [{i + 1}] id={d['id']} service={d['service']} type={d['type']}")

# 3. Descarga
if distributions:
    first = distributions[0]
    print(f"\n[3/3] Descargando distribucion {first['id']}...")
    downloaded = download_distribution(token, first)
    payload = downloaded['payload']
    keys = list(payload.keys()) if isinstance(payload, dict) else 'raw'
    print(f'      Descarga OK - payload keys: {keys}')
else:
    print('\n[3/3] Sin distribuciones para descargar')

print('\nIntegracion sandbox completada OK')
