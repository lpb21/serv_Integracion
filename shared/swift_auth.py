"""
swift_auth.py
Construye el JWT RS256 para autenticación OAuth2 con Swift
y obtiene el Bearer token del endpoint OAuth2.

Flujo validado contra sandbox.swift.com:
  1. En sandbox: descarga llaves del endpoint sandbox-selfsigned-dummy-secret
  2. Construye JWT con cryptography (NO PyJWT — control total del header x5c)
  3. POST con Basic Auth en header + JWT assertion en body
  4. Scope: swift.alliancecloud.api
  5. exp = now + 15 seg
"""

import os
import json
import time
import base64
import struct
import hashlib
import logging
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509 import load_pem_x509_certificate

logger = logging.getLogger(__name__)

SWIFT_ENV = os.environ.get("SWIFT_ENV", "sandbox")

_CONFIG = {
    "sandbox": {
        "base_url":  "https://sandbox.swift.com",
        "token_uri": "/oauth2/v1/token",
        "scope":     "swift.alliancecloud.api",
    },
    "test": {
        "base_url":  "https://api-test.swiftnet.sipn.swift.com",
        "token_uri": "/oauth2/v1/token",
        "scope":     "swift.alliancecloud.api",
    },
    "prod": {
        "base_url":  "https://api.swiftnet.sipn.swift.com",
        "token_uri": "/oauth2/v1/token",
        "scope":     "swift.alliancecloud.api",
    },
}

_cfg = _CONFIG[SWIFT_ENV]


# ── Helpers JWT ───────────────────────────────────────────────────────────

def _b64url(data: bytes) -> str:
    """Encode bytes a base64url sin padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _get_x5c(certificate_pem: str) -> str:
    """
    Procesa el certificado PEM al formato x5c para el JWT header.
    Remueve headers, footers y saltos de línea.
    """
    return (
        certificate_pem
        .replace("-----BEGIN CERTIFICATE-----", "")
        .replace("-----END CERTIFICATE-----", "")
        .replace("\n", "")
        .strip()
    )


def _get_subject_dn(certificate_pem: str) -> str:
    """
    Extrae el Subject DN del certificado X.509.
    En sandbox siempre es: CN=demo-swift-sandbox-consumer, O=Demo, ...
    """
    try:
        cert = load_pem_x509_certificate(certificate_pem.encode())
        attrs = []
        for attr in cert.subject:
            oid_dotted = attr.oid.dotted_string
            # Mapeo de OIDs comunes a abreviaciones
            _oid_map = {
                "2.5.4.3":  "CN",
                "2.5.4.6":  "C",
                "2.5.4.7":  "L",
                "2.5.4.8":  "S",
                "2.5.4.10": "O",
                "2.5.4.11": "OU",
            }
            short = _oid_map.get(oid_dotted, oid_dotted)
            attrs.append(f"{short}={attr.value}")
        return ", ".join(attrs)
    except Exception:
        return "CN=demo-swift-sandbox-consumer, O=Demo, L=London, S=London, C=GB"


def build_jwt(private_key_pem: str, certificate_pem: str, consumer_key: str) -> str:
    """
    Construye y firma el JWT RS256 para el JWT-Bearer grant.

    Args:
        private_key_pem:  Llave privada RSA en formato PEM
        certificate_pem:  Certificado X.509 en formato PEM
        consumer_key:     Consumer key del portal de Swift

    Returns:
        JWT firmado como string
    """
    x5c     = _get_x5c(certificate_pem)
    subject = _get_subject_dn(certificate_pem)
    now     = int(time.time())
    aud     = f"{_cfg['base_url']}{_cfg['token_uri']}".replace("https://", "")

    import secrets
    jti = secrets.token_urlsafe(16)

    header = {"alg": "RS256", "typ": "JWT", "x5c": [x5c]}
    payload = {
        "iss": consumer_key,
        "sub": subject,
        "aud": aud,
        "exp": now + 15,   # 15 seg — igual que sample oficial de Swift
        "iat": now,
        "nbf": now,
        "jti": jti,
    }

    h = _b64url(json.dumps(header,  separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}".encode()

    private_key = serialization.load_pem_private_key(
        private_key_pem.encode(), password=None
    )
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())

    return f"{h}.{p}.{_b64url(signature)}"


# ── Resolución de llaves ──────────────────────────────────────────────────

def resolve_keys(creds: dict) -> tuple[str, str]:
    """
    Devuelve (private_key_pem, certificate_pem).
    En sandbox descarga las llaves del endpoint público de Swift.
    En test/prod usa las llaves institucionales de DynamoDB.
    """
    if SWIFT_ENV == "sandbox":
        server = "https://sandbox.swift.com"

        pk_response = urllib.request.urlopen(
            f"{server}/sandbox-selfsigned-dummy-secret/privatekey2"
        )
        pk_raw = pk_response.read().decode().strip()
        private_key_pem = (
            f"-----BEGIN PRIVATE KEY-----\n{pk_raw}\n-----END PRIVATE KEY-----"
        )

        cert_response = urllib.request.urlopen(
            f"{server}/sandbox-selfsigned-dummy-secret/certificate2"
        )
        cert_raw = cert_response.read().decode().strip()
        certificate_pem = (
            f"-----BEGIN CERTIFICATE-----\n{cert_raw}\n-----END CERTIFICATE-----"
        )

        return private_key_pem, certificate_pem

    return creds["private_key_pem"], creds["certificate_pem"]


# ── Fetch token ───────────────────────────────────────────────────────────

def fetch_swift_token(creds: dict) -> dict:
    """
    Obtiene el Bearer token de Swift OAuth2.
    Autenticación: JWT-Bearer grant + Basic Auth en header.

    Args:
        creds: dict con consumer_key, consumer_secret, private_key_pem, certificate_pem

    Returns:
        dict con access_token, expires_in, refresh_token, ...
    """
    private_key_pem, certificate_pem = resolve_keys(creds)

    assertion = build_jwt(private_key_pem, certificate_pem, creds["consumer_key"])

    basic_auth = base64.b64encode(
        f"{creds['consumer_key']}:{creds['consumer_secret']}".encode()
    ).decode()

    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion":  assertion,
        "scope":      _cfg["scope"],
    }).encode()

    url = f"{_cfg['base_url']}{_cfg['token_uri']}"
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type":  "application/x-www-form-urlencoded",
            "Accept":        "application/json",
            "Authorization": f"Basic {basic_auth}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_err = e.read().decode()
        raise RuntimeError(f"Swift OAuth failed {e.code}: {body_err}") from e
