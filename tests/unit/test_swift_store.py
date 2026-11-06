"""Pruebas de shared/swift_store.py (DynamoDB y KMS simulados con mocks)."""

from unittest.mock import MagicMock

import pytest
from boto3.dynamodb.types import Binary

from shared import swift_store


@pytest.fixture
def store(monkeypatch):
    """swift_store con clientes falsos y caché limpia."""
    dynamo = MagicMock()
    kms = MagicMock()
    # "Cifrado" reversible y legible para las aserciones
    kms.encrypt.side_effect = lambda KeyId, Plaintext: {"CiphertextBlob": b"enc:" + Plaintext}
    kms.decrypt.side_effect = lambda CiphertextBlob, KeyId: {"Plaintext": CiphertextBlob[4:]}
    monkeypatch.setattr(swift_store, "_dynamo", dynamo)
    monkeypatch.setattr(swift_store, "_kms", kms)
    monkeypatch.setattr(swift_store, "_token_cache", None)
    monkeypatch.setattr(swift_store, "_token_cached_at", 0.0)
    monkeypatch.setattr(swift_store, "_creds_cache", None)
    monkeypatch.setattr(swift_store, "_creds_cached_at", 0.0)
    return swift_store, dynamo, kms


def test_kms_roundtrip(store):
    mod, _, _ = store
    assert mod._kms_decrypt(mod._kms_encrypt("secreto")) == "secreto"


def test_db_put_serializa_tipos(store):
    mod, dynamo, _ = store
    mod._db_put({"pk": "x", "b": b"\x01", "n": 5, "lista": ["a"]})
    item = dynamo.put_item.call_args.kwargs["Item"]
    assert item["pk"] == {"S": "x"}
    assert item["b"] == {"B": b"\x01"}
    assert item["n"] == {"N": "5"}
    assert item["lista"] == {"L": [{"S": "a"}]}


def test_db_get_sin_item(store):
    mod, dynamo, _ = store
    dynamo.get_item.return_value = {}
    assert mod._db_get("nada") is None


def test_db_get_convierte_binary_a_bytes(store):
    mod, dynamo, _ = store
    dynamo.get_item.return_value = {"Item": {"pk": {"S": "token"}, "access_token": {"B": b"enc:abc"}}}
    item = mod._db_get("token")
    assert item["access_token"] == b"enc:abc"
    assert not isinstance(item["access_token"], Binary)


def test_get_credentials_descifra_y_cachea(store):
    mod, dynamo, _ = store
    dynamo.get_item.return_value = {"Item": {
        "pk": {"S": "credentials"},
        "consumer_key": {"S": "ck"},
        "consumer_secret": {"B": b"enc:cs"},
        "private_key_pem": {"B": b"enc:pk"},
        "certificate_pem": {"B": b"enc:cert"},
    }}
    creds = mod.get_credentials()
    assert creds == {"consumer_key": "ck", "consumer_secret": "cs",
                     "private_key_pem": "pk", "certificate_pem": "cert"}
    mod.get_credentials()                       # segunda llamada: desde caché
    assert dynamo.get_item.call_count == 1


def test_get_credentials_inexistentes(store):
    mod, dynamo, _ = store
    dynamo.get_item.return_value = {}
    with pytest.raises(RuntimeError, match="Credentials not found"):
        mod.get_credentials()


def test_get_token_descifra_y_cachea(store):
    mod, dynamo, _ = store
    dynamo.get_item.return_value = {"Item": {
        "pk": {"S": "token"},
        "access_token": {"B": b"enc:tok"},
        "expires_in": {"N": "1799"},
        "obtained_at": {"S": "2026-11-06T00:00:00+00:00"},
    }}
    tok = mod.get_token()
    assert tok["access_token"] == "tok"
    assert tok["expires_in"] == 1799
    mod.get_token()
    assert dynamo.get_item.call_count == 1


def test_get_token_inexistente(store):
    mod, dynamo, _ = store
    dynamo.get_item.return_value = {"Item": {"pk": {"S": "token"}}}
    with pytest.raises(RuntimeError, match="Token not found"):
        mod.get_token()


def test_save_token_cifra_e_invalida_cache(store, monkeypatch):
    mod, dynamo, _ = store
    monkeypatch.setattr(mod, "_token_cache", {"access_token": "viejo"})
    mod.save_token({"access_token": "nuevo", "expires_in": 100})
    item = dynamo.put_item.call_args.kwargs["Item"]
    assert item["access_token"] == {"B": b"enc:nuevo"}
    assert item["expires_in"] == {"N": "100"}
    assert mod._token_cache is None


def test_save_credentials_cifra_campos_sensibles(store):
    mod, dynamo, _ = store
    mod.save_credentials({"consumer_key": "ck", "consumer_secret": "cs",
                          "private_key_pem": "pk", "certificate_pem": "cert"})
    item = dynamo.put_item.call_args.kwargs["Item"]
    assert item["consumer_key"] == {"S": "ck"}
    assert item["consumer_secret"] == {"B": b"enc:cs"}
    assert item["private_key_pem"] == {"B": b"enc:pk"}
    assert item["certificate_pem"] == {"B": b"enc:cert"}
    assert mod._creds_cache is None
