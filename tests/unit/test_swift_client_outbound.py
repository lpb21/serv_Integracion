"""Pruebas de shared/swift_client_outbound.py — envío a SWIFT (urlopen simulado)."""

import json

import pytest

from shared import swift_client_outbound as out
from conftest import FakeHTTPResponse, make_http_error

MT_MSG = {"message_type": "fin.103", "sender_reference": "R1", "sender": "BANKPAXXX000",
          "receiver": "BANAPAPAXXXX", "payload": "eA=="}
MX_MSG = {"message_type": "pacs.008.001.09", "sender_reference": "R2", "sender": "BANKPAXXXXXX",
          "receiver": "BANAPAPAXXXX", "payload": "eA=="}


@pytest.fixture
def http(monkeypatch):
    llamadas, respuestas = [], []

    def fake_urlopen(req, timeout=None):
        llamadas.append(req)
        r = respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return FakeHTTPResponse(r)

    monkeypatch.setattr(out.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(out.time, "sleep", lambda s: None)
    return llamadas, respuestas


@pytest.mark.parametrize("mtype,esperado", [
    ("fin.103", "fin"), ("apc.xyz", "fin"), ("pacs.008.001.09", "interAct"),
])
def test_detect_service(mtype, esperado):
    assert out.detect_service(mtype) == esperado


def test_detect_service_vacio():
    with pytest.raises(ValueError):
        out.detect_service("")


def test_swift_post_con_firma(http, rsa_pem_pair):
    llamadas, respuestas = http
    respuestas.append(b'{"message_cloud_reference":"MCR"}')
    r = out._swift_post("tok", "/fin/messages", {"a": 1}, rsa_pem_pair)
    assert r["message_cloud_reference"] == "MCR"
    assert llamadas[0].get_header("X-swift-signature").count(".") == 2


def test_swift_post_credenciales_placeholder_no_firma(http):
    llamadas, respuestas = http
    respuestas.append(b"{}")
    out._swift_post("tok", "/fin/messages", {"a": 1},
                    {"private_key_pem": "SANDBOX_PLACEHOLDER", "certificate_pem": "x"})
    assert llamadas[0].get_header("X-swift-signature") == ""


def test_swift_post_reintenta_transitorio(http):
    llamadas, respuestas = http
    respuestas.extend([make_http_error(503, b'{"status":{"severity":"Transient"}}'), b"{}"])
    out._swift_post("tok", "/fin/messages", {}, None)
    assert len(llamadas) == 2


def test_swift_post_error_anidado_en_status(http):
    _, respuestas = http
    respuestas.append(make_http_error(
        400, b'{"status":{"code":"SwAP09","text":"Missing signature","severity":"Fatal"}}'))
    with pytest.raises(out.SwiftApiError) as exc:
        out._swift_post("tok", "/fin/messages", {}, None)
    assert exc.value.swift_code == "SwAP09"
    assert exc.value.status_code == 400
    assert "Missing signature" in str(exc.value)


def test_swift_post_error_plano_y_body_invalido(http):
    _, respuestas = http
    respuestas.append(make_http_error(500, b"no-json"))
    with pytest.raises(out.SwiftApiError, match="unknown"):
        out._swift_post("tok", "/fin/messages", {}, None)


def test_swift_post_agota_reintentos(http):
    _, respuestas = http
    respuestas.extend([make_http_error(429, b"{}")] * 2)
    with pytest.raises(out.SwiftApiError):
        out._swift_post("tok", "/fin/messages", {}, None, max_retries=1)


def test_bic_to_dn(monkeypatch):
    monkeypatch.setenv("SWIFT_DN_OU", "ops")
    assert out._bic_to_dn("BANKPAXXXXXX") == "ou=ops,o=bankpaxx,o=swift"
    with pytest.raises(ValueError, match="cannot determine DN"):
        out._bic_to_dn("ABC")


def test_to_interact_body(monkeypatch):
    monkeypatch.setenv("SWIFT_INTERACT_SERVICE_CODE", "swift.finplus!pf")
    body = out._to_interact_body(MX_MSG)
    assert "sender" not in body and "receiver" not in body
    assert body["service_code"] == "swift.finplus!pf"
    assert body["requestor"].endswith("o=bankpaxx,o=swift")
    assert body["responder"].endswith("o=banapapa,o=swift")
    assert body["format"] == "MX"


def test_to_interact_body_sin_service_code(monkeypatch):
    monkeypatch.delenv("SWIFT_INTERACT_SERVICE_CODE", raising=False)
    with pytest.raises(ValueError, match="SWIFT_INTERACT_SERVICE_CODE"):
        out._to_interact_body(MX_MSG)


def test_send_messages_individual_fin_e_interact(http):
    llamadas, respuestas = http
    respuestas.extend([b'{"r":"fin"}', b'{"r":"mx"}'])
    assert out.send_messages("tok", MT_MSG, None) == {"r": "fin"}
    assert out.send_messages("tok", MX_MSG, None) == {"r": "mx"}
    assert llamadas[0].full_url.endswith("/fin/messages")
    assert llamadas[1].full_url.endswith("/interact/messages")
    assert "service_code" in json.loads(llamadas[1].data)


def test_send_messages_batch_parte_en_bloques(http, monkeypatch):
    llamadas, respuestas = http
    monkeypatch.setattr(out, "FIN_BATCH_MAX", 2)
    monkeypatch.setattr(out, "INTERACT_BATCH_MAX", 2)
    respuestas.extend([b'{"results":[1,2]}', b'{"results":[3]}',
                       b'{"results":["a","b"]}', b'{"results":["c"]}'])
    assert out.send_messages("tok", [MT_MSG] * 3, None) == [1, 2, 3]
    assert out.send_messages("tok", [MX_MSG] * 3, None) == ["a", "b", "c"]
    assert llamadas[0].full_url.endswith("/fin/messages/batch")
    assert llamadas[2].full_url.endswith("/interact/messages/batch")


def test_send_messages_validaciones():
    with pytest.raises(ValueError, match="No messages"):
        out.send_messages("tok", [], None)
    with pytest.raises(ValueError, match="same service"):
        out.send_messages("tok", [MT_MSG, MX_MSG], None)
