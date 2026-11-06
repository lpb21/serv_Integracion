"""Casos borde de shared/mt_parser.py y shared/mx_parser.py (complementa test_parsers.py)."""

import base64

import pytest

from shared import mt_parser, mx_parser
from shared.mt_parser import parse_mt_message
from shared.mx_parser import parse_mx_message, detect_message_format

APPHDR = (
    '<AppHdr xmlns="urn:iso:std:iso:20022:tech:xsd:head.001.001.02">'
    '<Fr><FIId><FinInstnId><BICFI>BANKPAXXXXX</BICFI></FinInstnId></FIId></Fr>'
    '<To><FIId><FinInstnId><BICFI>BANAPAPA</BICFI></FinInstnId></FIId></To>'
    '<BizMsgIdr>{ref}</BizMsgIdr><MsgDefIdr>pacs.008.001.09</MsgDefIdr>'
    '<BizSvc>swift.cbprplus.02</BizSvc></AppHdr>'
)
DOC = ('<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.09">'
       '<UETR>uuid-1</UETR></Document>')


# ── MT ─────────────────────────────────────────────────────────────────────

def test_mt_entrada_vacia():
    with pytest.raises(ValueError, match="non-empty"):
        parse_mt_message("")


def test_mt_sin_bloque_2():
    with pytest.raises(ValueError, match="Block 2"):
        parse_mt_message("{1:F01BANKPAXXX0000000000}{4:\r\n:20:X\r\n-}")


def test_mt_sin_bloque_4():
    with pytest.raises(ValueError, match="Block 4"):
        parse_mt_message("{1:F01BANKPAXXX0000000000}{2:I103BANAPAPAXXXXN}")


def test_mt_bloque_2_invalido():
    with pytest.raises(ValueError, match="Could not parse Block 2"):
        parse_mt_message("{2:X103BANAPAPAXXXXN}{4:\r\n:20:X\r\n-}")


def test_mt_mensaje_entrante_se_rechaza():
    with pytest.raises(ValueError, match="Incoming"):
        parse_mt_message("{2:O1031200BANKPAXXX}{4:\r\n:20:X\r\n-}")


def test_mt_sin_bloque_3_genera_referencia_auto_y_sin_bloque_1():
    r = parse_mt_message("{2:I900BANAPAPAXXXX}{4:\n:20:X\n-}")
    assert r["sender_reference"].startswith("AUTO-")
    assert r["sender"] is None
    assert r["network_info"]["network_priority"] == "Normal"
    assert base64.b64decode(r["payload"]).decode().startswith(":20:X")


def test_mt_referencia_demasiado_larga():
    ref = "R" * 71
    with pytest.raises(ValueError, match="exceeds 70"):
        parse_mt_message(f"{{2:I103BANAPAPAXXXXN}}{{3:{{108:{ref}}}}}{{4:\r\n:20:X\r\n-}}")


def test_mt_fallback_bloque_4_con_llaves_anidadas():
    blocks = mt_parser._parse_blocks("{2:I103BANAPAPAXXXXN}{4:\r\n:20:X{Y}\r\n-}{5:{CHK:1}}")
    assert "4" in blocks


def test_mt_helpers():
    assert mt_parser._parse_block1("corto") is None
    assert mt_parser._parse_block2("") is None
    assert mt_parser._parse_block3("") == {}
    assert mt_parser._extract_block4("sin-salto") == "sin-salto"
    with pytest.raises(ValueError):
        mt_parser._extract_block4("")
    assert mt_parser._build_message_type("103", "stp") == "fin.103.STP"
    assert mt_parser._build_message_type("103", "OTRO") == "fin.103"


# ── MX ─────────────────────────────────────────────────────────────────────

def test_mx_con_wrapper_extrae_apphdr_y_document():
    raw = f"<Envelope>{APPHDR.format(ref='REF-9')}{DOC}</Envelope>"
    r = parse_mx_message(raw)
    payload = base64.b64decode(r["payload"]).decode()
    assert "<Envelope>" not in payload
    assert payload.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert r["sender"] == "BANKPAXXXXXX"          # BIC11 -> BIC12
    assert r["receiver"] == "BANAPAPAXXXX"        # BIC8 -> BIC12
    assert r["network_info"] == {"request_type": "pacs.008.001.09",
                                 "request_subtype": "swift.cbprplus.02"}
    assert r["_meta"]["uetr"] == "uuid-1"


@pytest.mark.parametrize("raw,mensaje", [
    ("", "non-empty"),
    (f"<x>{DOC}</x>", "AppHdr"),
    (APPHDR.format(ref="R"), "Document"),
    (APPHDR.format(ref="R").replace("<MsgDefIdr>pacs.008.001.09</MsgDefIdr>", "") + DOC, "MsgDefIdr"),
    (APPHDR.format(ref="") + DOC, "BizMsgIdr"),
    (APPHDR.format(ref="R" * 71) + DOC, "exceeds 70"),
    (APPHDR.format(ref="R").replace("<To>", "<Otro>").replace("</To>", "</Otro>") + DOC, "receiver BIC"),
])
def test_mx_errores(raw, mensaje):
    with pytest.raises(ValueError, match=mensaje):
        parse_mx_message(raw)


def test_mx_helpers():
    assert mx_parser._normalize_bic(None) is None
    assert mx_parser._normalize_bic("abcd") == "ABCDXXXXXXXX"
    assert mx_parser._extract_sender_bic("<AppHdr></AppHdr>") is None
    assert mx_parser._extract_tag("<a>1</a>", "b") is None


def test_detect_format_casos():
    assert detect_message_format('<Doc xmlns="urn:iso:std:iso:20022:x"/>') == "MX"
    with pytest.raises(ValueError):
        detect_message_format("")
    with pytest.raises(ValueError, match="Cannot detect"):
        detect_message_format("texto plano")
