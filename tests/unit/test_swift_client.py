"""Pruebas de shared/swift_client.py — operaciones inbound (urlopen simulado)."""

import json

import pytest

from shared import swift_client
from conftest import FakeHTTPResponse, make_http_error


@pytest.fixture
def http(monkeypatch):
    """Cola de respuestas: cada elemento es (bytes, headers) o una excepción."""
    llamadas, respuestas = [], []

    def fake_urlopen(req, timeout=None):
        llamadas.append(req)
        r = respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        body, headers = r
        return FakeHTTPResponse(body, headers)

    monkeypatch.setattr(swift_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(swift_client.time, "sleep", lambda s: None)
    return llamadas, respuestas


def test_swift_request_ok_con_integrity(http):
    llamadas, respuestas = http
    respuestas.append((b'{"ok":true}', {"X-SWIFT-Integrity": "INT"}))
    data, integrity = swift_client._swift_request("tok", "/distributions")
    assert data == {"ok": True} and integrity == "INT"
    assert llamadas[0].full_url == swift_client.BASE_URL + "/distributions"
    assert llamadas[0].get_header("Authorization") == "Bearer tok"


def test_swift_request_body_vacio_y_url_absoluta(http):
    llamadas, respuestas = http
    respuestas.append((b"", {}))
    data, _ = swift_client._swift_request("tok", "https://otra.url/x", method="POST", body={})
    assert data == {}
    assert llamadas[0].full_url == "https://otra.url/x"


def test_swift_request_reintenta_transitorios(http):
    llamadas, respuestas = http
    respuestas.extend([
        make_http_error(429, b"{}"),
        make_http_error(503, b'{"severity":"Transient"}'),
        (b'{"ok":1}', {}),
    ])
    data, _ = swift_client._swift_request("tok", "/x")
    assert data == {"ok": 1} and len(llamadas) == 3


def test_swift_request_error_fatal_expone_atributos(http):
    _, respuestas = http
    respuestas.append(make_http_error(400, b'{"code":"SwAP01","text":"bad","severity":"Fatal"}'))
    with pytest.raises(RuntimeError) as exc:
        swift_client._swift_request("tok", "/x")
    assert exc.value.status_code == 400
    assert exc.value.swift_code == "SwAP01"
    assert "bad" in str(exc.value)


def test_swift_request_error_body_no_json(http):
    _, respuestas = http
    respuestas.append(make_http_error(404, b"<html>"))
    with pytest.raises(RuntimeError, match="unknown"):
        swift_client._swift_request("tok", "/x")


def test_swift_request_agota_reintentos(http):
    _, respuestas = http
    respuestas.extend([make_http_error(429, b"{}")] * 2)
    with pytest.raises(RuntimeError, match="429"):
        swift_client._swift_request("tok", "/x", max_retries=1)


def test_list_distributions_paginado(http):
    llamadas, respuestas = http
    pagina1 = {"distributions": [{"id": i} for i in range(100)], "links": [{"rel": "next"}]}
    pagina2 = {"distributions": [{"id": 100}], "links": []}
    respuestas.extend([(json.dumps(pagina1).encode(), {}), (json.dumps(pagina2).encode(), {})])
    dists = swift_client.list_distributions("tok")
    assert len(dists) == 101
    assert "offset=100" in llamadas[1].full_url


@pytest.mark.parametrize("service,dist_type,path,method", [
    ("fin", "message", "/fin/messages/7", "GET"),
    ("interAct", "deliveryReport", "/interact/delivery-reports/7", "GET"),
    ("fileAct", "message", "/fileact/messages/7", "POST"),
])
def test_resolve_download_path(service, dist_type, path, method):
    assert swift_client._resolve_download_path(7, service, dist_type) == (path, method)


def test_resolve_download_path_desconocido():
    with pytest.raises(ValueError, match="Unknown distribution"):
        swift_client._resolve_download_path(7, "fin", "noexiste")


def test_download_distribution_usa_id_real(http):
    llamadas, respuestas = http
    body = {"distribution": {"id": 555}, "message": "UEFZTE9BRA=="}
    respuestas.append((json.dumps(body).encode(), {"X-SWIFT-Integrity": "I"}))
    r = swift_client.download_distribution("tok", {"id": 7, "service": "fin", "type": "message"})
    assert r == {"payload": "UEFZTE9BRA==", "integrity": "I", "distribution_id": 555,
                 "service": "fin", "dist_type": "message"}
    assert llamadas[0].full_url.endswith("/fin/messages/7")


def test_download_distribution_fileact_envia_callback(http, monkeypatch):
    llamadas, respuestas = http
    monkeypatch.setenv("FILEACT_CALLBACK_URL", "https://cb.test")
    respuestas.append((b'{"message":"x"}', {}))
    r = swift_client.download_distribution("tok", {"id": 9, "service": "fileAct", "type": "message"})
    assert r["distribution_id"] == 9
    assert json.loads(llamadas[0].data) == {"callback_url": "https://cb.test"}
    assert llamadas[0].get_method() == "POST"


def test_ack_nak_y_batch(http):
    llamadas, respuestas = http
    respuestas.extend([(b"", {}), (b"", {}), (b'{"updated":2}', {})])
    swift_client.ack_distribution("tok", 1)
    swift_client.nak_distribution("tok", 2, "x" * 300)
    data = swift_client.batch_update_distributions("tok", [{"id": 1}, {"id": 2}])
    assert llamadas[0].full_url.endswith("/distributions/1/acks")
    assert llamadas[1].full_url.endswith("/distributions/2/naks")
    assert len(json.loads(llamadas[1].data)["message"]) == 200
    assert llamadas[2].get_method() == "PATCH"
    assert data == {"updated": 2}
