"""
swift_client_outbound.py
Cliente HTTP para envío de mensajes a Swift — operaciones outbound.

Endpoints:
  POST /fin/messages              → FIN individual
  POST /fin/messages/batch        → FIN batch (hasta 500)
  POST /interact/messages         → InterAct individual
  POST /interact/messages/batch   → InterAct batch (hasta 50)
  POST /fileact/messages          → FileAct
"""

import os
import json
import time
import logging
import urllib.request
import urllib.error
from typing import Optional

from shared.swift_signature import build_swift_signature

logger = logging.getLogger(__name__)

class SwiftApiError(RuntimeError):
    """
    Error al invocar la API de Swift.

    Atributos para diagnóstico:
      status_code : código HTTP de la respuesta (429, 500, etc.)
      swift_code  : código de error propio de Swift (del body)
      severity    : severidad reportada por Swift (Transient / Fatal ...)
      text        : mensaje descriptivo de Swift
    """
    def __init__(self, message, status_code=None, swift_code=None,
                 severity=None, text=None):
        super().__init__(message)
        self.status_code = status_code
        self.swift_code = swift_code
        self.severity = severity
        self.text = text

from shared.swift_env import SWIFT_ENV

_BASE_URLS = {
    "sandbox": "https://sandbox.swift.com/alliancecloud/v2",
    "test":    "https://api-test.swiftnet.sipn.swift.com/alliancecloud/v2",
    "prod":    "https://api.swiftnet.sipn.swift.com/alliancecloud/v2",
}

BASE_URL = _BASE_URLS[SWIFT_ENV]

FIN_BATCH_MAX      = 500
INTERACT_BATCH_MAX = 50


def detect_service(message_type: str) -> str:
    """
    Detecta si un mensaje es FIN o InterAct por el message_type.
      FIN:      fin.103, fin.202, fin.202.COV, fin.900, fin.910, apc.*
      InterAct: pacs.008, pacs.009, camt.*, pain.*, ...
    """
    if not message_type:
        raise ValueError("message_type is required")
    if message_type.startswith("fin.") or message_type.startswith("apc."):
        return "fin"
    return "interAct"


def _swift_post(
    token: str,
    path: str,
    body: dict,
    credentials: Optional[dict] = None,
    max_retries: int = 3,
) -> dict:
    """HTTP POST a Swift con X-SWIFT-Signature y retry."""
    url         = f"{BASE_URL}{path}"
    body_string = json.dumps(body, separators=(",", ":"))
    body_bytes  = body_string.encode()

    def _usable(pem):
        return bool(pem) and "BEGIN" in pem

    has_real_creds = bool(credentials) and _usable(credentials.get("private_key_pem"))

    if credentials and not has_real_creds:
        logger.warning("Credenciales PKI no utilizables (env=%s) — enviando sin X-SWIFT-Signature", SWIFT_ENV)

    signature = (
        build_swift_signature(url, body_string, credentials)
        if has_real_creds else ""
    )

    for attempt in range(max_retries + 1):
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers={
                "Authorization":    f"Bearer {token}",
                "Content-Type":     "application/json",
                "Accept":           "application/json",
                "X-SWIFT-Signature": signature,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())

        # después
        except urllib.error.HTTPError as e:
            raw_error = b""
            try:
                raw_error = e.read()
            except Exception:
                pass

            logger.error(
                "Swift HTTP %s en %s — headers=%s body=%s",
                e.code, path, dict(e.headers), raw_error[:2000].decode("utf-8", "replace"),
            )

            try:
                error_body = json.loads(raw_error)
            except Exception:
                error_body = {}

            severity     = error_body.get("severity", "")
            is_transient = e.code == 429 or (e.code >= 500 and severity == "Transient")

            if is_transient and attempt < max_retries:
                delay = 0.5 * (2 ** attempt)
                logger.warning("Swift upload transient error (attempt %d), retry in %.1fs",
                               attempt + 1, delay)
                time.sleep(delay)
                continue

            raise SwiftApiError(
                f"Swift upload error {e.code}: {error_body.get('text', 'unknown')}",
                status_code=e.code,
                swift_code=error_body.get("code"),
                severity=severity,
                text=error_body.get("text"),
            )

    raise SwiftApiError("Max retries exceeded", severity="Transient")


def send_fin_message(token: str, message: dict, credentials: dict) -> dict:
    """POST /fin/messages — FIN individual."""
    return _swift_post(token, "/fin/messages", message, credentials)


def send_fin_message_batch(token: str, messages: list, credentials: dict) -> list:
    """POST /fin/messages/batch — hasta 500 mensajes FIN."""
    results = []
    for i in range(0, len(messages), FIN_BATCH_MAX):
        chunk = messages[i:i + FIN_BATCH_MAX]
        data  = _swift_post(token, "/fin/messages/batch", {"messages": chunk}, credentials)
        results.extend(data.get("results", []))
    return results


def send_interact_message(token: str, message: dict, credentials: dict) -> dict:
    """POST /interact/messages — InterAct individual."""
    return _swift_post(token, "/interact/messages", message, credentials)


def send_interact_message_batch(token: str, messages: list, credentials: dict) -> list:
    """POST /interact/messages/batch — hasta 50 mensajes InterAct."""
    results = []
    for i in range(0, len(messages), INTERACT_BATCH_MAX):
        chunk = messages[i:i + INTERACT_BATCH_MAX]
        data  = _swift_post(token, "/interact/messages/batch", {"messages": chunk}, credentials)
        results.extend(data.get("results", []))
    return results


def send_messages(token: str, messages, credentials: dict):
    """
    Router principal — detecta servicio y envía.

    Args:
        messages: dict (individual) o list (batch)
    """
    is_list = isinstance(messages, list)
    lst     = messages if is_list else [messages]

    if not lst:
        raise ValueError("No messages to send")

    service = detect_service(lst[0]["message_type"])
    if not all(detect_service(m["message_type"]) == service for m in lst):
        raise ValueError("All messages in a batch must be the same service")

    if service == "fin":
        result = (
            send_fin_message(token, lst[0], credentials)
            if len(lst) == 1
            else send_fin_message_batch(token, lst, credentials)
        )
    else:
        result = (
            send_interact_message(token, lst[0], credentials)
            if len(lst) == 1
            else send_interact_message_batch(token, lst, credentials)
        )

    return result
