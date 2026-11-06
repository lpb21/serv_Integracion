"""
conftest.py — configuración común de las pruebas unitarias.

- Define las variables de entorno que los módulos leen AL IMPORTARSE
  (swift_store exige DYNAMODB_TABLE_NAME y KMS_KEY_ID; swift_env usa SWIFT_ENV).
- Agrega la raíz del repo al sys.path para importar `shared.*`.
- Expone fixtures para cargar los handlers de las Lambdas (todos se llaman
  handler.py, así que se cargan por ruta con nombres de módulo únicos) y un
  par llave/certificado RSA generado en memoria para firmar.

Ninguna prueba toca AWS ni SWIFT: boto3 y urllib se reemplazan con mocks.
"""

import datetime
import importlib.util
import os
import sys

import pytest

# ── Entorno (antes de importar cualquier módulo de shared/) ────────────────
os.environ.setdefault("SWIFT_ENV", "dev")                 # dev -> sandbox
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("DYNAMODB_TABLE_NAME", "swift-sqs-test-store")
os.environ.setdefault("KMS_KEY_ID", "alias/swift-sqs-test-store")
os.environ.setdefault("SQS_CORE_INBOUND_URL", "https://sqs.test/core-inbound.fifo")
os.environ.setdefault("SQS_SEND_RESULTS_URL", "https://sqs.test/send-results.fifo")
os.environ.setdefault("DISPATCHER_FUNCTION_NAME", "swift-sqs-test-message-dispatcher")
os.environ.setdefault("SWIFT_INTERACT_SERVICE_CODE", "swift.finplus!pf")

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def _load_handler(lambda_dir: str):
    path = os.path.join(REPO_ROOT, "lambdas", lambda_dir, "handler.py")
    name = f"handler_{lambda_dir}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def load_handler():
    return _load_handler


@pytest.fixture(scope="session")
def rsa_pem_pair():
    """Llave privada + certificado autofirmado (PEM) para pruebas de firma."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "unit-test-consumer"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Banistmo"),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "PA"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    return {"private_key_pem": key_pem, "certificate_pem": cert_pem, "public_key": key.public_key()}


class FakeHTTPResponse:
    """Respuesta mínima compatible con `with urllib.request.urlopen(...) as resp`."""

    def __init__(self, body: bytes = b"", headers=None):
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def make_http_error(code: int, body: bytes = b""):
    import io
    import urllib.error
    return urllib.error.HTTPError("https://swift.test", code, "error", {}, io.BytesIO(body))
