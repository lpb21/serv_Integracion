"""
mt_parser.py
Parsea un mensaje MT completo con todos los bloques SWIFT.

Input (texto plano del body SQS del core):
  {1:F01BANKPAXXX0000000000}{2:I103BANAPAPAXXXXN}
  {3:{108:TXN-001}{121:uuid}}
  {4:\\r\\n:20:TXN-001\\r\\n:23B:CRED\\r\\n...\\r\\n-}{5:{CHK:...}}

Output (formato del spec Swift Messaging API v2.1.0):
  {
    "sender_reference": "TXN-001",
    "message_type":     "fin.103",
    "sender":           "BANKPAXXX000",
    "receiver":         "BANAPAPAXXXX",
    "payload":          "<base64 bloque 4>",
    "network_info":     {"network_priority": "Normal"},
    "_meta":            {...}
  }
"""

import re
import base64
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_PRIORITY_MAP = {"N": "Normal", "U": "Urgent", "S": "System"}
_VARIANT_FLAGS = {"COV", "REMIT", "STP"}


def _parse_blocks(raw_mt: str) -> dict:
    """
    Extrae los bloques del mensaje MT.
    Retorna dict: {'1': contenido, '2': contenido, ...}
    """
    blocks = {}
    pattern = re.compile(r'\{(\d):([\s\S]*?)\}(?=\{\d:|$)')
    for m in pattern.finditer(raw_mt):
        blocks[m.group(1)] = m.group(2)

    # Fallback para bloque 4 con llaves anidadas
    if "4" not in blocks:
        b4 = re.search(r'\{4:([\s\S]*?)\r?\n-\}', raw_mt)
        if b4:
            blocks["4"] = b4.group(1)

    return blocks


def _parse_block1(b1: str) -> Optional[dict]:
    """
    Parsea el bloque 1 (Basic Header).
    Formato: F01BANKPAXXX0000000000
    """
    if not b1 or len(b1) < 10:
        return None
    return {
        "application_id": b1[0],
        "service_id":     b1[1:3],
        "sender_bic12":   b1[3:15].ljust(12, "X"),
    }


def _parse_block2(b2: str) -> Optional[dict]:
    """
    Parsea el bloque 2 (Application Header).
    Outgoing: I103BANAPAPAXXXXN
    """
    if not b2:
        return None

    direction = b2[0]

    if direction == "I":
        msg_type_code = b2[1:4]
        receiver_bic  = b2[4:16].ljust(12, "X")
        priority_char = b2[16] if len(b2) > 16 else "N"
        return {
            "direction":        "Outgoing",
            "message_type_code": msg_type_code,
            "receiver_bic12":   receiver_bic,
            "network_priority": _PRIORITY_MAP.get(priority_char.upper(), "Normal"),
        }

    if direction == "O":
        return {
            "direction":        "Incoming",
            "message_type_code": b2[1:4],
        }

    return None


def _parse_block3(b3: str) -> dict:
    """
    Parsea el bloque 3 (User Header).
    Sub-bloques: {108:MUR}{119:STP}{121:uuid}
    """
    if not b3:
        return {}

    fields = {}
    for m in re.finditer(r'\{(\d{3}):([^}]*)\}', b3):
        fields[m.group(1)] = m.group(2).strip()

    return {
        "mur":             fields.get("108"),   # sender_reference
        "validation_flag": fields.get("119"),   # COV, REMIT, STP...
        "uetr":            fields.get("121"),   # UUID ISO 20022
        "raw":             fields,
    }


def _extract_block4(b4: str) -> str:
    """
    Extrae y limpia el bloque 4.
    El spec espera solo el bloque 4 en base64 — sin el \r\n inicial.
    """
    if not b4:
        raise ValueError("Block 4 (text block) is missing from MT message")

    text = b4
    if text.startswith("\r\n"):
        text = text[2:]
    elif text.startswith("\n"):
        text = text[1:]
    return text


def _build_message_type(type_code: str, validation_flag: Optional[str]) -> str:
    """
    Construye el message_type del spec:
    "fin.103", "fin.202", "fin.202.COV", "fin.103.REMIT", "fin.900"
    """
    base = f"fin.{type_code}"
    if validation_flag and validation_flag.upper() in _VARIANT_FLAGS:
        return f"{base}.{validation_flag.upper()}"
    return base


def parse_mt_message(raw_mt: str) -> dict:
    """
    Parser principal — toma el mensaje MT completo y retorna
    el objeto listo para enviar al spec de Swift Messaging API.

    Args:
        raw_mt: Mensaje MT completo con bloques {1:...}{2:...}{3:...}{4:...}{5:...}

    Returns:
        dict con sender_reference, message_type, sender, receiver,
        payload (base64), network_info y _meta
    """
    if not raw_mt or not isinstance(raw_mt, str):
        raise ValueError("raw_mt must be a non-empty string")

    blocks = _parse_blocks(raw_mt)

    if "2" not in blocks:
        raise ValueError("Block 2 (Application Header) is missing")
    if "4" not in blocks:
        raise ValueError("Block 4 (Text Block) is missing")

    b1 = _parse_block1(blocks.get("1", ""))
    b2 = _parse_block2(blocks["2"])
    b3 = _parse_block3(blocks.get("3", ""))

    if not b2:
        raise ValueError("Could not parse Block 2")

    if b2["direction"] != "Outgoing":
        raise ValueError(
            f"MT message direction is '{b2['direction']}' — only Outgoing messages should be sent"
        )

    sender_ref = b3.get("mur") or f"AUTO-{int(__import__('time').time())}"
    if len(sender_ref) > 70:
        raise ValueError(f"sender_reference exceeds 70 chars: {sender_ref}")

    block4_text = _extract_block4(blocks["4"])
    payload_b64 = base64.b64encode(block4_text.encode("utf-8")).decode("ascii")

    return {
        "sender_reference": sender_ref,
        "message_type":     _build_message_type(
            b2["message_type_code"], b3.get("validation_flag")
        ),
        "sender":   b1["sender_bic12"] if b1 else None,
        "receiver": b2["receiver_bic12"],
        "payload":  payload_b64,
        "network_info": {
            "network_priority": b2["network_priority"],
        },
        "_meta": {
            "uetr":              b3.get("uetr"),
            "validation_flag":   b3.get("validation_flag"),
            "message_type_code": b2["message_type_code"],
            "raw_blocks":        blocks,
        },
    }
