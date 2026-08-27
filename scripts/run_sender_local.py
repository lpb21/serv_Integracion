"""
scripts/run_sender_local.py
Simula un evento SQS para probar el message-sender localmente
con mensajes MT103 y pacs.008 de prueba.

Uso:
  export $(grep -v "^#" .env.local | xargs)
  python scripts/run_sender_local.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import boto3  # noqa: E402
from lambdas.message_sender.handler import lambda_handler  # noqa: E402

MT103 = (
    '{1:F01BANKPAXXX0000000000}{2:I103BANAPAPAXXXXN}'
    '{3:{108:TXN-LOCAL-001}{121:550e8400-e29b-41d4-a716-446655440001}}'
    '{4:\r\n:20:TXN-LOCAL-001\r\n:23B:CRED\r\n:32A:250115USD15000,\r\n'
    ':50K:/123456789\r\nBANCO ORIGEN PANAMA\r\n:59:/987654321\r\n'
    'BENEFICIARIO EJEMPLO\r\n:70:PAGO PRUEBA LOCAL\r\n:71A:SHA\r\n-}'
    '{5:{CHK:ABCDEF123456}}'
)

PACS008 = """<?xml version="1.0" encoding="UTF-8"?>
<AppHdr xmlns="urn:iso:std:iso:20022:tech:xsd:head.001.001.02">
  <Fr><FIId><FinInstnId><BICFI>BANKPAXX</BICFI></FinInstnId></FIId></Fr>
  <To><FIId><FinInstnId><BICFI>BANAPAPAXXXX</BICFI></FinInstnId></FIId></To>
  <BizMsgIdr>MSG-LOCAL-001</BizMsgIdr>
  <MsgDefIdr>pacs.008.001.09</MsgDefIdr>
  <CreDt>2025-01-15T10:30:00Z</CreDt>
</AppHdr>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.09">
  <FIToFICstmrCdtTrf>
    <GrpHdr><MsgId>MSG-LOCAL-001</MsgId><CreDtTm>2025-01-15T10:30:00Z</CreDtTm><NbOfTxs>1</NbOfTxs></GrpHdr>
  </FIToFICstmrCdtTrf>
</Document>"""


def make_sqs_event(messages):
    return {
        'Records': [
            {
                'messageId':         f'local-test-{i + 1}',
                'receiptHandle':     f'receipt-{i + 1}',
                'body':              body,
                'attributes':        {},
                'messageAttributes': {},
            }
            for i, body in enumerate(messages)
        ]
    }


print('\n=== message-sender local test ===\n')

print('[1/2] Enviando MT103...')
try:
    result = lambda_handler(make_sqs_event([MT103]), None)
    print(f'      MT103 result: {json.dumps(result)}')
except Exception as e:
    print(f'      MT103 error: {e}')

print('\n[2/2] Enviando pacs.008...')
try:
    result = lambda_handler(make_sqs_event([PACS008]), None)
    print(f'      pacs.008 result: {json.dumps(result)}')
except Exception as e:
    print(f'      pacs.008 error: {e}')

# Verificar mensajes en send-results
print('\n--- Mensajes en send-results ---')
sqs = boto3.client('sqs', endpoint_url=os.environ.get('AWS_ENDPOINT_URL'))
try:
    response = sqs.receive_message(
        QueueUrl=os.environ['SQS_SEND_RESULTS_URL'],
        MaxNumberOfMessages=10,
        WaitTimeSeconds=2,
    )
    messages = response.get('Messages', [])
    if messages:
        for i, m in enumerate(messages):
            body = json.loads(m['Body'])
            print(f"\n[{i + 1}] {body.get('format')} {body.get('message_type')}")
            print(f"    sender_reference:        {body.get('sender_reference')}")
            print(f"    message_cloud_reference: {body.get('message_cloud_reference')}")
            print(f"    status:                  {body.get('status')}")
            if body.get('status') == 'Failed':
                print(f"    error:                   {body.get('error_text')}")
    else:
        print('    No hay mensajes en send-results')
except Exception as e:
    print(f'    Error leyendo send-results: {e}')
