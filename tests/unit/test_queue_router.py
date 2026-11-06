"""Pruebas de shared/queue_router.py"""

import base64
import json

import pytest

from shared import queue_router


def test_resolve_inbound_queue_url_fin_y_interact(monkeypatch):
    monkeypatch.setenv("SQS_CORE_INBOUND_URL", "https://sqs.test/in.fifo")
    assert queue_router.resolve_inbound_queue_url("fin") == "https://sqs.test/in.fifo"
    assert queue_router.resolve_inbound_queue_url("interAct") == "https://sqs.test/in.fifo"


def test_resolve_inbound_queue_url_servicio_no_soportado():
    with pytest.raises(ValueError, match="no soportado"):
        queue_router.resolve_inbound_queue_url("fileAct")


def test_resolve_inbound_queue_url_sin_configurar(monkeypatch):
    monkeypatch.setenv("SQS_CORE_INBOUND_URL", "")
    with pytest.raises(ValueError, match="no configurada"):
        queue_router.resolve_inbound_queue_url("fin")


def test_build_sqs_message_decodifica_base64_y_campos_fifo():
    texto = ":20:REF-1\r\n:32A:250115USD1,"
    msg = queue_router.build_sqs_message({
        "payload": base64.b64encode(texto.encode()).decode(),
        "integrity": "abc123",
        "distribution_id": 987,
        "service": "fin",
        "dist_type": "message",
    })
    assert msg["message_body"] == texto
    assert msg["message_group_id"] == "fin"
    assert msg["message_deduplication_id"] == "987"
    attrs = msg["message_attributes"]
    assert attrs["distributionId"]["StringValue"] == "987"
    assert attrs["integrityToken"]["StringValue"] == "abc123"
    assert attrs["distributionType"]["StringValue"] == "message"


def test_build_sqs_message_payload_no_base64_y_sin_integrity():
    payload = {"no": "base64"}
    msg = queue_router.build_sqs_message({
        "payload": payload,
        "integrity": None,
        "distribution_id": 1,
        "service": "interAct",
        "dist_type": None,
    })
    assert msg["message_body"] == json.dumps(payload)
    assert "integrityToken" not in msg["message_attributes"]
    assert msg["message_attributes"]["distributionType"]["StringValue"] == ""
