"""
swift_store.py
Reemplaza Secrets Manager. Usa DynamoDB + KMS para guardar
credenciales Swift y el Bearer token con cifrado de campo.

Tabla DynamoDB: swift-sqs-{env}-store
Partition Key:  pk (String)

Ítem "credentials":
  pk:              "credentials"
  consumer_key:    str  ← en claro
  consumer_secret: bytes ← cifrado KMS
  private_key_pem: bytes ← cifrado KMS
  certificate_pem: bytes ← cifrado KMS
  updated_at:      str  ISO timestamp

Ítem "token":
  pk:           "token"
  access_token: bytes ← cifrado KMS
  expires_in:   int   ← en claro
  obtained_at:  str   ← en claro

Variables de entorno requeridas:
  DYNAMODB_TABLE_NAME
  KMS_KEY_ID
  AWS_REGION (inyectada por Lambda automáticamente)
"""

import os
import time
import logging
from datetime import datetime, timezone
from typing import Optional

import boto3
from boto3.dynamodb.types import TypeSerializer, TypeDeserializer, Binary
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
KMS_KEY_ID = os.environ["KMS_KEY_ID"]

_dynamo = boto3.client("dynamodb")
_kms    = boto3.client("kms")

# ── Caché en memoria ──────────────────────────────────────────────────────
_token_cache:       Optional[dict] = None
_token_cached_at:   float          = 0.0
_creds_cache:       Optional[dict] = None
_creds_cached_at:   float          = 0.0

TOKEN_TTL  = 5 * 60   # 5 min: menor que el intervalo de refresco (25 min)
CREDS_TTL  = 60 * 60   # 60 min

_serializer   = TypeSerializer()
_deserializer = TypeDeserializer()


# ── KMS helpers ───────────────────────────────────────────────────────────

def _kms_encrypt(plaintext: str) -> bytes:
    """Cifra un string con KMS. Devuelve ciphertext como bytes."""
    response = _kms.encrypt(
        KeyId=KMS_KEY_ID,
        Plaintext=plaintext.encode("utf-8"),
    )
    return response["CiphertextBlob"]


def _kms_decrypt(ciphertext: bytes) -> str:
    """Descifra bytes cifrados con KMS. Devuelve string."""
    response = _kms.decrypt(
        CiphertextBlob=ciphertext,
        KeyId=KMS_KEY_ID,
    )
    return response["Plaintext"].decode("utf-8")


# ── DynamoDB helpers ──────────────────────────────────────────────────────

def _db_get(pk: str) -> Optional[dict]:
    """Lee un ítem de DynamoDB por partition key."""
    response = _dynamo.get_item(
        TableName=TABLE_NAME,
        Key={"pk": {"S": pk}},
    )
    item = response.get("Item")
    if not item:
        return None
    result = {}
    for k, v in item.items():
        deserialized = _deserializer.deserialize(v)
        # TypeDeserializer devuelve los Binary como objeto Binary de boto3;
        # KMS espera bytes puro, así que lo convertimos.
        if isinstance(deserialized, Binary):
            deserialized = bytes(deserialized)
        result[k] = deserialized
    return result


def _db_put(item: dict) -> None:
    """Escribe un ítem en DynamoDB."""
    dynamo_item = {}
    for k, v in item.items():
        if isinstance(v, (bytes, bytearray)):
            dynamo_item[k] = {"B": v}
        elif isinstance(v, str):
            dynamo_item[k] = {"S": v}
        elif isinstance(v, int):
            dynamo_item[k] = {"N": str(v)}
        else:
            dynamo_item[k] = _serializer.serialize(v)

    _dynamo.put_item(TableName=TABLE_NAME, Item=dynamo_item)


# ── API pública ───────────────────────────────────────────────────────────

def get_credentials() -> dict:
    """
    Lee y descifra las credenciales Swift desde DynamoDB.
    Usa caché en memoria para contenedores Lambda calientes.

    Returns:
        dict con consumer_key, consumer_secret, private_key_pem, certificate_pem
    """
    global _creds_cache, _creds_cached_at

    now = time.monotonic()
    if _creds_cache and (now - _creds_cached_at) < CREDS_TTL:
        return _creds_cache

    item = _db_get("credentials")
    if not item:
        raise RuntimeError(
            "Credentials not found in DynamoDB. Run scripts/setup_credentials.py first."
        )

    # Descifrar campos sensibles
    consumer_secret = _kms_decrypt(item["consumer_secret"])
    private_key_pem = _kms_decrypt(item["private_key_pem"])
    certificate_pem = _kms_decrypt(item["certificate_pem"])

    _creds_cache = {
        "consumer_key":    item["consumer_key"],   # en claro
        "consumer_secret": consumer_secret,
        "private_key_pem": private_key_pem,
        "certificate_pem": certificate_pem,
    }
    _creds_cached_at = now
    return _creds_cache


def get_token() -> dict:
    """
    Lee y descifra el Bearer token desde DynamoDB.
    Usa caché en memoria de 45 min.

    Returns:
        dict con access_token, expires_in, obtained_at
    """
    global _token_cache, _token_cached_at

    now = time.monotonic()
    if _token_cache and (now - _token_cached_at) < TOKEN_TTL:
        return _token_cache

    item = _db_get("token")
    if not item or "access_token" not in item:
        raise RuntimeError(
            "Token not found in DynamoDB. Invoke token-refresher first."
        )

    access_token = _kms_decrypt(item["access_token"])

    _token_cache = {
        "access_token": access_token,
        "expires_in":   int(item.get("expires_in", 1799)),
        "obtained_at":  item.get("obtained_at", ""),
    }
    _token_cached_at = now
    return _token_cache


def save_token(token_data: dict) -> None:
    """
    Cifra y guarda el Bearer token en DynamoDB.
    Llamado por token-refresher después de obtener el token de Swift.
    """
    global _token_cache, _token_cached_at

    encrypted_token = _kms_encrypt(token_data["access_token"])

    _db_put({
        "pk":           "token",
        "access_token": encrypted_token,
        "expires_in":   int(token_data.get("expires_in", 1799)),
        "obtained_at":  datetime.now(timezone.utc).isoformat(),
    })

    # Invalidar caché
    _token_cache    = None
    _token_cached_at = 0.0

    logger.info("swift_store: token saved to DynamoDB")


def save_credentials(creds: dict) -> None:
    """
    Cifra y guarda las credenciales Swift en DynamoDB.
    Se llama UNA VEZ durante el setup inicial o al rotar credenciales.

    Args:
        creds: dict con consumer_key, consumer_secret,
               private_key_pem, certificate_pem
    """
    global _creds_cache, _creds_cached_at

    enc_secret  = _kms_encrypt(creds["consumer_secret"])
    enc_pem_key = _kms_encrypt(creds["private_key_pem"])
    enc_pem_cert= _kms_encrypt(creds["certificate_pem"])

    _db_put({
        "pk":              "credentials",
        "consumer_key":    creds["consumer_key"],   # en claro
        "consumer_secret": enc_secret,
        "private_key_pem": enc_pem_key,
        "certificate_pem": enc_pem_cert,
        "updated_at":      datetime.now(timezone.utc).isoformat(),
    })

    # Invalidar caché
    _creds_cache    = None
    _creds_cached_at = 0.0

    logger.info("swift_store: credentials saved to DynamoDB")
