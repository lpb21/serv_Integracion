"""Pruebas de shared/swift_signature.py"""

import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from shared import swift_signature


def _b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def test_calculate_digest_es_sha256_de_base64_del_body():
    body = '{"a":1}'
    esperado = base64.b64encode(
        hashlib.sha256(base64.b64encode(body.encode())).digest()
    ).decode()
    assert swift_signature._calculate_digest(body) == esperado


def test_get_x5c_quita_cabeceras_y_saltos(rsa_pem_pair):
    x5c = swift_signature._get_x5c(rsa_pem_pair["certificate_pem"])
    assert "BEGIN" not in x5c and "\n" not in x5c
    base64.b64decode(x5c)  # debe ser base64 válido


def test_get_subject_dn_de_certificado_real(rsa_pem_pair):
    dn = swift_signature._get_subject_dn(rsa_pem_pair["certificate_pem"])
    assert "CN=unit-test-consumer" in dn
    assert "O=Banistmo" in dn


def test_get_subject_dn_invalido_en_sandbox_devuelve_demo(monkeypatch):
    monkeypatch.setattr(swift_signature, "SWIFT_ENV", "sandbox")
    assert "demo-swift-sandbox-consumer" in swift_signature._get_subject_dn("no-es-un-pem")


def test_get_subject_dn_invalido_fuera_de_sandbox_falla(monkeypatch):
    monkeypatch.setattr(swift_signature, "SWIFT_ENV", "prod")
    with pytest.raises(Exception):
        swift_signature._get_subject_dn("no-es-un-pem")


def test_build_swift_signature_firma_verificable(rsa_pem_pair):
    url = "https://sandbox.swift.com/alliancecloud/v2/fin/messages"
    body = '{"sender_reference":"REF-1"}'
    jws = swift_signature.build_swift_signature(url, body, rsa_pem_pair)

    h, p, s = jws.split(".")
    header = json.loads(_b64url_decode(h))
    payload = json.loads(_b64url_decode(p))

    assert header["alg"] == "RS256"
    assert payload["aud"] == "sandbox.swift.com/alliancecloud/v2/fin/messages"
    assert payload["digest"] == swift_signature._calculate_digest(body)
    assert payload["exp"] - payload["iat"] == 15

    # La firma debe validar con la llave pública
    rsa_pem_pair["public_key"].verify(
        _b64url_decode(s), f"{h}.{p}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )
