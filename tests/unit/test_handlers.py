"""Pruebas de los 4 handlers de Lambda (dependencias simuladas con mocks)."""

import json
from unittest.mock import MagicMock

import pytest

MT103 = (
    '{1:F01BANKPAXXX0000000000}{2:I103BANAPAPAXXXXN}'
    '{3:{108:TEST-001}{121:550e8400-e29b-41d4-a716-446655440001}}'
    '{4:\r\n:20:TEST-001\r\n:23B:CRED\r\n:32A:250115USD15000,\r\n-}'
)


# ── token-refresher ────────────────────────────────────────────────────────

def test_token_refresher(load_handler, monkeypatch):
    h = load_handler("token_refresher")
    guardado = {}
    monkeypatch.setattr(h, "get_credentials", lambda: {"consumer_key": "ck"})
    monkeypatch.setattr(h, "fetch_swift_token", lambda c: {"access_token": "t", "expires_in": 1799})
    monkeypatch.setattr(h, "save_token", lambda t: guardado.update(t))
    r = h.lambda_handler({}, None)
    assert r["statusCode"] == 200
    assert guardado["access_token"] == "t"


# ── message-dispatcher ─────────────────────────────────────────────────────

def test_dispatcher_publica_y_hace_ack(load_handler, monkeypatch):
    h = load_handler("message_dispatcher")
    sqs = MagicMock()
    sqs.send_message.return_value = {"MessageId": "m-1"}
    monkeypatch.setattr(h, "_sqs_client", sqs)
    monkeypatch.setattr(h, "get_token", lambda: {"access_token": "tok"})
    monkeypatch.setattr(h, "download_distribution", lambda tok, d: {
        "payload": "OjIwOlg=", "integrity": None, "distribution_id": 555,
        "service": "fin", "dist_type": "message"})
    acks = []
    monkeypatch.setattr(h, "ack_distribution", lambda tok, i: acks.append(i))

    r = h.lambda_handler({"distributionId": 7, "service": "fin", "type": "message"}, None)

    assert r == {"statusCode": 200, "distributionId": 7}
    kwargs = sqs.send_message.call_args.kwargs
    assert kwargs["MessageBody"] == ":20:X"
    assert kwargs["MessageGroupId"] == "fin"
    assert kwargs["MessageDeduplicationId"] == "555"
    assert acks == [555]                       # ACK con el id REAL


def test_dispatcher_no_hace_ack_si_falla_sqs(load_handler, monkeypatch):
    h = load_handler("message_dispatcher")
    sqs = MagicMock()
    sqs.send_message.side_effect = RuntimeError("SQS caído")
    monkeypatch.setattr(h, "_sqs_client", sqs)
    monkeypatch.setattr(h, "get_token", lambda: {"access_token": "tok"})
    monkeypatch.setattr(h, "download_distribution", lambda tok, d: {
        "payload": "eA==", "integrity": None, "distribution_id": 1,
        "service": "fin", "dist_type": "message"})
    acks = []
    monkeypatch.setattr(h, "ack_distribution", lambda tok, i: acks.append(i))
    with pytest.raises(RuntimeError):
        h.lambda_handler({"distributionId": 1, "service": "fin", "type": "message"}, None)
    assert acks == []


def test_dispatcher_crea_cliente_sqs_una_vez(load_handler, monkeypatch):
    h = load_handler("message_dispatcher")
    creados = []
    monkeypatch.setattr(h.boto3, "client", lambda svc: creados.append(svc) or MagicMock())
    monkeypatch.setattr(h, "_sqs_client", None)
    c1, c2 = h._get_sqs_client(), h._get_sqs_client()
    assert c1 is c2 and creados == ["sqs"]


# ── message-poller ─────────────────────────────────────────────────────────

def test_poller_sin_distribuciones(load_handler, monkeypatch):
    h = load_handler("message_poller")
    monkeypatch.setattr(h, "get_token", lambda: {"access_token": "tok"})
    monkeypatch.setattr(h, "list_distributions", lambda tok: [])
    assert h.lambda_handler({}, None)["message"] == "No distributions available"


def test_poller_despacha_y_cuenta_fallos(load_handler, monkeypatch):
    h = load_handler("message_poller")
    lam = MagicMock()

    def invoke(**kwargs):
        payload = json.loads(kwargs["Payload"])
        assert kwargs["InvocationType"] == "Event"
        if payload["distributionId"] == 2:
            raise RuntimeError("throttled")

    lam.invoke.side_effect = invoke
    monkeypatch.setattr(h, "_lambda_client", lam)
    monkeypatch.setattr(h, "get_token", lambda: {"access_token": "tok"})
    monkeypatch.setattr(h, "list_distributions", lambda tok: [
        {"id": 1, "service": "fin", "type": "message"},
        {"id": 2, "service": "interAct", "type": "message", "copy": True},
        {"id": 3, "service": "fin", "type": "deliveryReport"},
    ])
    r = h.lambda_handler({}, None)
    assert r == {"statusCode": 200, "dispatched": 2, "failed": 1}


def test_poller_crea_cliente_lambda_una_vez(load_handler, monkeypatch):
    h = load_handler("message_poller")
    creados = []
    monkeypatch.setattr(h.boto3, "client", lambda svc: creados.append(svc) or MagicMock())
    monkeypatch.setattr(h, "_lambda_client", None)
    assert h._get_lambda_client() is h._get_lambda_client()
    assert creados == ["lambda"]


# ── message-sender ─────────────────────────────────────────────────────────

@pytest.fixture
def sender(load_handler, monkeypatch):
    h = load_handler("message_sender")
    sqs = MagicMock()
    monkeypatch.setattr(h, "_sqs_client", sqs)
    monkeypatch.setattr(h, "get_token", lambda: {"access_token": "tok"})
    monkeypatch.setattr(h, "get_credentials", lambda: {
        "private_key_pem": "PK", "certificate_pem": "CERT"})
    return h, sqs


def _evento(*bodies):
    return {"Records": [{"messageId": f"sqs-{i}", "body": b} for i, b in enumerate(bodies)]}


def test_sender_ok_publica_resultado(sender, monkeypatch):
    h, sqs = sender
    monkeypatch.setattr(h, "send_messages",
                        lambda tok, msg, cred: {"message_cloud_reference": "MCR-1"})
    r = h.lambda_handler(_evento(MT103), None)
    assert r == {"batchItemFailures": []}
    kwargs = sqs.send_message.call_args.kwargs
    body = json.loads(kwargs["MessageBody"])
    assert body["status"] == "Accepted"
    assert body["message_cloud_reference"] == "MCR-1"
    assert kwargs["MessageGroupId"] == "fin"
    assert kwargs["MessageDeduplicationId"] == "TEST-001-Accepted"


def test_sender_error_de_parseo_es_fatal(sender):
    h, sqs = sender
    r = h.lambda_handler(_evento("esto no es MT ni MX"), None)
    assert r == {"batchItemFailures": []}           # fatal: no se reintenta
    body = json.loads(sqs.send_message.call_args.kwargs["MessageBody"])
    assert body["status"] == "Failed" and body["error_code"] == "PARSE_ERROR"


def test_sender_error_transitorio_se_reintenta(sender, monkeypatch):
    h, sqs = sender

    def falla(tok, msg, cred):
        raise h.SwiftApiError("Swift upload error 503", status_code=503, swift_code="SwGW01")

    monkeypatch.setattr(h, "send_messages", falla)
    r = h.lambda_handler(_evento(MT103), None)
    assert r == {"batchItemFailures": [{"itemIdentifier": "sqs-0"}]}
    body = json.loads(sqs.send_message.call_args.kwargs["MessageBody"])
    assert body["error_code"] == "SwGW01"
    assert body["sender_reference"] == "TEST-001"


def test_sender_error_4xx_es_fatal(sender, monkeypatch):
    h, _ = sender

    def falla(tok, msg, cred):
        raise h.SwiftApiError("Swift upload error 400", status_code=400)

    monkeypatch.setattr(h, "send_messages", falla)
    assert h.lambda_handler(_evento(MT103), None) == {"batchItemFailures": []}


def test_sender_publish_error_no_propaga_fallo_de_sqs(sender):
    h, sqs = sender
    sqs.send_message.side_effect = RuntimeError("SQS caído")
    h.publish_error("sqs-x", ValueError("x"))      # no debe lanzar


def test_transform_message_vacio(sender):
    h, _ = sender
    with pytest.raises(ValueError, match="empty"):
        h.transform_message("   ")


def test_transform_message_formato_desconocido(sender, monkeypatch):
    h, _ = sender
    monkeypatch.setattr(h, "detect_message_format", lambda raw: "OTRO")
    with pytest.raises(ValueError, match="Cannot determine"):
        h.transform_message("x")


@pytest.mark.parametrize("error,fatal", [
    (type("E", (Exception,), {"status_code": 404})("x"), True),
    (type("E", (Exception,), {"status_code": 502})("x"), False),
    (ValueError("Block 4 is MISSING"), True),
    (ConnectionError("timeout"), False),
])
def test_is_fatal(sender, error, fatal):
    h, _ = sender
    assert h.is_fatal(error) is fatal


def test_sender_crea_cliente_sqs_una_vez(load_handler, monkeypatch):
    h = load_handler("message_sender")
    creados = []
    monkeypatch.setattr(h.boto3, "client", lambda svc: creados.append(svc) or MagicMock())
    monkeypatch.setattr(h, "_sqs_client", None)
    assert h._get_sqs_client() is h._get_sqs_client()
    assert creados == ["sqs"]
