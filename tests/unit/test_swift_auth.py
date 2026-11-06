"""Pruebas de shared/swift_auth.py (sin red: urlopen simulado)."""

import base64
import json
import urllib.parse

import pytest

from shared import swift_auth
from conftest import FakeHTTPResponse, make_http_error


def _decode(part):
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def test_get_x5c_y_subject_dn(rsa_pem_pair):
    assert "BEGIN" not in swift_auth._get_x5c(rsa_pem_pair["certificate_pem"])
    assert "CN=unit-test-consumer" in swift_auth._get_subject_dn(rsa_pem_pair["certificate_pem"])


def test_get_subject_dn_invalido_devuelve_demo():
    assert "demo-swift-sandbox-consumer" in swift_auth._get_subject_dn("basura")


def test_build_jwt_claims(rsa_pem_pair):
    jwt = swift_auth.build_jwt(rsa_pem_pair["private_key_pem"], rsa_pem_pair["certificate_pem"], "mi-consumer")
    h, p, _ = jwt.split(".")
    payload = _decode(p)
    assert _decode(h)["alg"] == "RS256"
    assert payload["iss"] == "mi-consumer"
    assert payload["aud"] == "sandbox.swift.com/oauth2/v1/token"
    assert payload["exp"] - payload["iat"] == 15


def test_resolve_keys_fuera_de_sandbox_usa_dynamo(monkeypatch):
    monkeypatch.setattr(swift_auth, "SWIFT_ENV", "prod")
    assert swift_auth.resolve_keys({"private_key_pem": "K", "certificate_pem": "C"}) == ("K", "C")


def test_resolve_keys_sandbox_descarga_llaves_dummy(monkeypatch):
    monkeypatch.setattr(swift_auth, "SWIFT_ENV", "sandbox")
    urls = []

    def fake_urlopen(url, timeout=None):
        urls.append(url)
        return FakeHTTPResponse(b"PRIVKEY" if "privatekey" in url else b"CERT")

    monkeypatch.setattr(swift_auth.urllib.request, "urlopen", fake_urlopen)
    pk, cert = swift_auth.resolve_keys({})
    assert pk.startswith("-----BEGIN PRIVATE KEY-----") and "PRIVKEY" in pk
    assert cert.startswith("-----BEGIN CERTIFICATE-----") and "CERT" in cert
    assert len(urls) == 2


def test_fetch_swift_token_ok(monkeypatch, rsa_pem_pair):
    monkeypatch.setattr(swift_auth, "resolve_keys",
                        lambda c: (rsa_pem_pair["private_key_pem"], rsa_pem_pair["certificate_pem"]))
    capturado = {}

    def fake_urlopen(req, timeout=None):
        capturado["req"] = req
        return FakeHTTPResponse(json.dumps({"access_token": "t", "expires_in": 1799}).encode())

    monkeypatch.setattr(swift_auth.urllib.request, "urlopen", fake_urlopen)
    tok = swift_auth.fetch_swift_token({"consumer_key": "ck", "consumer_secret": "cs"})

    assert tok["access_token"] == "t"
    req = capturado["req"]
    assert req.full_url == "https://sandbox.swift.com/oauth2/v1/token"
    assert req.get_header("Authorization") == "Basic " + base64.b64encode(b"ck:cs").decode()
    form = urllib.parse.parse_qs(req.data.decode())
    assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    assert form["scope"] == ["swift.alliancecloud.api/access_to_service"]


def test_fetch_swift_token_error_http(monkeypatch, rsa_pem_pair):
    monkeypatch.setattr(swift_auth, "resolve_keys",
                        lambda c: (rsa_pem_pair["private_key_pem"], rsa_pem_pair["certificate_pem"]))

    def fake_urlopen(req, timeout=None):
        raise make_http_error(401, b'{"error":"invalid_client"}')

    monkeypatch.setattr(swift_auth.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="Swift OAuth failed 401"):
        swift_auth.fetch_swift_token({"consumer_key": "ck", "consumer_secret": "cs"})
