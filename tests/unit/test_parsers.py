"""
tests/unit/test_parsers.py
Tests unitarios de los parsers MT y MX - sin dependencias externas.

Uso: python -m pytest tests/unit/ -v
  o: python tests/unit/test_parsers.py
"""

import base64
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from shared.mt_parser import parse_mt_message  # noqa: E402
from shared.mx_parser import parse_mx_message, detect_message_format  # noqa: E402

MT103 = (
    '{1:F01BANKPAXXX0000000000}{2:I103BANAPAPAXXXXN}'
    '{3:{108:TEST-001}{121:550e8400-e29b-41d4-a716-446655440001}}'
    '{4:\r\n:20:TEST-001\r\n:23B:CRED\r\n:32A:250115USD15000,\r\n'
    ':50K:/123456789\r\nORDENANTE\r\n:59:/987654321\r\nBENEFICIARIO\r\n'
    ':71A:SHA\r\n-}'
    '{5:{CHK:ABCDEF123456}}'
)

MT202COV = (
    '{1:F01BANKPAXXX0000000000}{2:I202BANAPAPAXXXXU}'
    '{3:{108:COV-001}{119:COV}}'
    '{4:\r\n:20:COV-001\r\n:21:REL-001\r\n:32A:250115USD9000,\r\n-}'
    '{5:{CHK:AAA}}'
)

PACS008 = """<?xml version="1.0" encoding="UTF-8"?>
<AppHdr xmlns="urn:iso:std:iso:20022:tech:xsd:head.001.001.02">
  <Fr><FIId><FinInstnId><BICFI>BANKPAXX</BICFI></FinInstnId></FIId></Fr>
  <To><FIId><FinInstnId><BICFI>BANAPAPAXXXX</BICFI></FinInstnId></FIId></To>
  <BizMsgIdr>MSG-001</BizMsgIdr>
  <MsgDefIdr>pacs.008.001.09</MsgDefIdr>
  <CreDt>2025-01-15T10:30:00Z</CreDt>
</AppHdr>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.09">
  <FIToFICstmrCdtTrf><GrpHdr><MsgId>MSG-001</MsgId></GrpHdr></FIToFICstmrCdtTrf>
</Document>"""


def test_mt103():
    r = parse_mt_message(MT103)
    assert r['sender_reference'] == 'TEST-001', r['sender_reference']
    assert r['message_type'] == 'fin.103', r['message_type']
    assert r['sender'] == 'BANKPAXXX000', r['sender']
    assert r['receiver'] == 'BANAPAPAXXXX', r['receiver']
    assert r['network_info']['network_priority'] == 'Normal'
    assert r['_meta']['uetr'] == '550e8400-e29b-41d4-a716-446655440001'
    decoded = base64.b64decode(r['payload']).decode()
    assert decoded.startswith(':20:TEST-001'), decoded[:30]
    assert '{1:' not in decoded  # solo bloque 4
    print('OK test_mt103')


def test_mt202cov():
    r = parse_mt_message(MT202COV)
    assert r['message_type'] == 'fin.202.COV', r['message_type']
    assert r['network_info']['network_priority'] == 'Urgent'
    print('OK test_mt202cov')


def test_pacs008():
    r = parse_mx_message(PACS008)
    assert r['sender_reference'] == 'MSG-001', r['sender_reference']
    assert r['message_type'] == 'pacs.008.001.09', r['message_type']
    assert r['sender'] == 'BANKPAXXXXXX', r['sender']       # BIC8 -> BIC12
    assert r['receiver'] == 'BANAPAPAXXXX', r['receiver']   # BIC12 sin cambio
    decoded = base64.b64decode(r['payload']).decode()
    assert '<AppHdr' in decoded and '<Document' in decoded
    print('OK test_pacs008')


def test_detect_format():
    assert detect_message_format(MT103) == 'MT'
    assert detect_message_format(PACS008) == 'MX'
    print('OK test_detect_format')


if __name__ == '__main__':
    test_mt103()
    test_mt202cov()
    test_pacs008()
    test_detect_format()
    print('\nTodos los tests pasaron OK')
