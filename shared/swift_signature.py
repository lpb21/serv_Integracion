"""
swift_signature.py
Construye el header X-SWIFT-Signature requerido para envío de mensajes.

El X-SWIFT-Signature es un JWS RS256 que incluye:
  sub:    Subject DN del certificado
  aud:    URL del endpoint sin https://
  exp:    now + 15 seg
  iat, nbf: now
  jti:    nonce aleatorio
  digest: SHA256(base64(body_string)) — vincula la firma al payload
"""

import os
import json
import time
import base64
import hashlib
import secrets
import logging

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509 import load_pem_x509_certificate
from shared.swift_env import SWIFT_ENV

logger = logging.getLogger(__name__)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _get_x5c(certificate_pem: str) -> str:
    return (
        certificate_pem
        .replace("-----BEGIN CERTIFICATE-----", "")
        .replace("-----END CERTIFICATE-----", "")
        .replace("\n", "")
        .strip()
    )


def _get_subject_dn(certificate_pem: str) -> str:
    try:
        cert = load_pem_x509_certificate(certificate_pem.encode())
        _oid_map = {
            "2.5.4.3":  "CN", "2.5.4.6":  "C",  "2.5.4.7":  "L",
            "2.5.4.8":  "S",  "2.5.4.10": "O",  "2.5.4.11": "OU",
        }
        attrs = [
            f"{_oid_map.get(a.oid.dotted_string, a.oid.dotted_string)}={a.value}"
            for a in cert.subject
        ]
        return ", ".join(attrs)
    except Exception:
        if SWIFT_ENV == "sandbox":
            return "CN=demo-swift-sandbox-consumer, O=Demo, L=London, S=London, C=GB"
        raise


def _calculate_digest(body_string: str) -> str:
    """
    digest = SHA256(base64(body_string)) → base64
    Igual que calculateDigestSha256() del sample oficial de Swift.
    """
    data_b64 = base64.b64encode(body_string.encode("utf-8"))
    digest    = hashlib.sha256(data_b64).digest()
    return base64.b64encode(digest).decode("ascii")


def build_swift_signature(url: str, body_string: str, credentials: dict) -> str:
    """
    Construye el header X-SWIFT-Signature (JWS RS256).

    Args:
        url:         URL completa del endpoint (con https://)
        body_string: Body JSON serializado con json.dumps(body)
        credentials: dict con private_key_pem y certificate_pem

    Returns:
        JWS firmado para usar como valor del header X-SWIFT-Signature
    """
    private_key_pem = credentials["private_key_pem"]
    certificate_pem = credentials["certificate_pem"]

    x5c     = _get_x5c(certificate_pem)
    subject = _get_subject_dn(certificate_pem)
    now     = int(time.time())
    aud     = url.replace("https://", "")

    header = {"alg": "RS256", "typ": "JWT", "x5c": [x5c]}
    payload = {
        "sub":    subject,
        "aud":    aud,
        "exp":    now + 15,
        "iat":    now,
        "nbf":    now,
        "jti":    secrets.token_urlsafe(16),
        "digest": _calculate_digest(body_string),
    }

    h = _b64url(json.dumps(header,  separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}".encode()

    private_key = serialization.load_pem_private_key(
        private_key_pem.encode(), password=None
    )
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())

    return f"{h}.{p}.{_b64url(signature)}"
