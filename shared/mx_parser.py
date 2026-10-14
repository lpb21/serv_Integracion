"""
mx_parser.py
Parsea un mensaje MX completo (ISO 20022) con AppHdr (head.001) + Document.

Input (texto plano del body SQS del core):
  <?xml version="1.0" encoding="UTF-8"?>
  <AppHdr xmlns="urn:iso:std:iso:20022:tech:xsd:head.001.001.02">
    <Fr><FIId><FinInstnId><BICFI>BANKPAXX</BICFI>...
    <To><FIId><FinInstnId><BICFI>BANAPAPAXXXX</BICFI>...
    <BizMsgIdr>MSG-001</BizMsgIdr>
    <MsgDefIdr>pacs.008.001.09</MsgDefIdr>
    <CreDt>2025-01-15T10:30:00Z</CreDt>
  </AppHdr>
  <Document xmlns="urn:iso:std:iso:20022:...">...</Document>
"""

import re
import base64
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _extract_tag(xml: str, tag_name: str) -> Optional[str]:
    """Extrae el texto de un tag XML sin namespace."""
    patterns = [
        rf'<[^>]*:?{re.escape(tag_name)}[^>]*>([^<]*)</[^>]*:?{re.escape(tag_name)}>',
        rf'<{re.escape(tag_name)}>([^<]*)</{re.escape(tag_name)}>',
    ]
    for pat in patterns:
        m = re.search(pat, xml, re.DOTALL)
        if m:
            return m.group(1).strip()
    return None


def _extract_apphdr(xml: str) -> Optional[str]:
    m = re.search(r'<(?:[^:>]+:)?AppHdr[\s\S]*?</(?:[^:>]+:)?AppHdr>', xml)
    return m.group(0) if m else None


def _extract_document(xml: str) -> Optional[str]:
    m = re.search(r'<(?:[^:>]+:)?Document[\s\S]*?</(?:[^:>]+:)?Document>', xml)
    return m.group(0) if m else None


def _normalize_bic(bic: Optional[str]) -> Optional[str]:
    """Normaliza BIC a BIC12."""
    if not bic:
        return None
    clean = bic.strip().upper()
    if len(clean) == 8:
        return clean + "XXXX"
    if len(clean) == 11:
        return clean + "X"
    return (clean + "X" * 12)[:12]


def _extract_apphdr_namespace(apphdr_xml: str) -> Optional[str]:
    m = re.search(r'xmlns(?::[^=]+)?="([^"]+)"', apphdr_xml)
    return m.group(1) if m else None


def _extract_sender_bic(apphdr_xml: str) -> Optional[str]:
    fr_block = re.search(
        r'<(?:[^:>]+:)?Fr[\s\S]*?</(?:[^:>]+:)?Fr>|'
        r'<(?:[^:>]+:)?From[\s\S]*?</(?:[^:>]+:)?From>',
        apphdr_xml,
    )
    if fr_block:
        return _normalize_bic(_extract_tag(fr_block.group(0), "BICFI"))
    return None


def _extract_receiver_bic(apphdr_xml: str) -> Optional[str]:
    to_block = re.search(
        r'<(?:[^:>]+:)?To[\s\S]*?</(?:[^:>]+:)?To>', apphdr_xml
    )
    if to_block:
        return _normalize_bic(_extract_tag(to_block.group(0), "BICFI"))
    return None


def _build_payload_xml(raw_mx: str, apphdr_xml: str, document_xml: str) -> str:
    """
    Construye el payload XML limpio (AppHdr + Document sin wrapper externo).
    """
    xml_decl_match = re.search(r'<\?xml[^?]*\?>', raw_mx)
    xml_decl = xml_decl_match.group(0) if xml_decl_match else '<?xml version="1.0" encoding="UTF-8"?>'

    # Quitar declaración y comentarios para ver cuál es el primer elemento real
    body = re.sub(r'<\?xml[^?]*\?>', '', raw_mx, count=1)
    body = re.sub(r'<!--[\s\S]*?-->', '', body).strip()

    # Sin wrapper solo si el primer elemento ES el AppHdr
    if re.match(r'^<(?:[^:>]+:)?AppHdr[\s>]', body):
        return raw_mx

    return f"{xml_decl}\n{apphdr_xml}\n{document_xml}"


def detect_message_format(raw: str) -> str:
    """
    Detecta si el mensaje es MT o MX.
    MT: empieza con {1:, {2: o {
    MX: empieza con <?xml o contiene namespace ISO 20022
    """
    if not raw:
        raise ValueError("Empty message")

    trimmed = raw.strip()

    if (trimmed.startswith("{1:") or
            trimmed.startswith("{2:") or
            trimmed.startswith("{")):
        return "MT"

    if (trimmed.startswith("<?xml") or
            "urn:iso:std:iso:20022" in trimmed):
        return "MX"

    raise ValueError("Cannot detect message format — does not look like MT or MX")


def parse_mx_message(raw_mx: str) -> dict:
    """
    Parser principal — toma el XML MX completo y retorna
    el objeto listo para enviar al spec de Swift Messaging API.

    Args:
        raw_mx: XML MX completo con AppHdr + Document

    Returns:
        dict con sender_reference, message_type, sender, receiver,
        payload (base64) y _meta
    """
    if not raw_mx or not isinstance(raw_mx, str):
        raise ValueError("raw_mx must be a non-empty string")

    apphdr_xml = _extract_apphdr(raw_mx)
    if not apphdr_xml:
        raise ValueError("AppHdr (head.001) not found in MX message")

    document_xml = _extract_document(raw_mx)
    if not document_xml:
        raise ValueError("Document element not found in MX message")

    biz_msg_idr = _extract_tag(apphdr_xml, "BizMsgIdr")
    msg_def_idr = _extract_tag(apphdr_xml, "MsgDefIdr")
    cre_dt      = _extract_tag(apphdr_xml, "CreDt")

    if not msg_def_idr:
        raise ValueError("AppHdr/MsgDefIdr not found — cannot determine message type")

    sender_ref = biz_msg_idr
    if not sender_ref:
        raise ValueError("BizMsgIdr is empty — cannot determine sender_reference")
    if len(sender_ref) > 70:
        raise ValueError(f"sender_reference exceeds 70 chars: {sender_ref}")

    sender_bic   = _extract_sender_bic(apphdr_xml)
    receiver_bic = _extract_receiver_bic(apphdr_xml)
    if not receiver_bic:
        raise ValueError("Could not extract receiver BIC from AppHdr/To")

    payload_xml = _build_payload_xml(raw_mx, apphdr_xml, document_xml)
    payload_b64 = base64.b64encode(payload_xml.encode("utf-8")).decode("ascii")

    return {
        "sender_reference": sender_ref,
        "message_type":     msg_def_idr,
        "sender":           sender_bic,
        "receiver":         receiver_bic,
        "payload":          payload_b64,
        "_meta": {
            "biz_msg_idr":      biz_msg_idr,
            "msg_def_idr":      msg_def_idr,
            "cre_dt":           cre_dt,
            "sender_bic":       sender_bic,
            "receiver_bic":     receiver_bic,
            "apphdr_namespace": _extract_apphdr_namespace(apphdr_xml),
        },
    }
