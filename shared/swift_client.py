"""
swift_client.py
Cliente HTTP para la Swift Messaging API v2.1.0 — operaciones inbound.

Cubre:
  GET  /distributions          → lista distribuciones pendientes (paginado)
  Download por link rel:download
  POST /distributions/{id}/acks  → ACK
  POST /distributions/{id}/naks  → NAK
  PATCH /distributions           → batch ACK/NAK
"""

import os
import json
import time
import logging
import urllib.request
import urllib.error
from typing import Optional

logger = logging.getLogger(__name__)

# """SWIFT_ENV = os.environ.get("SWIFT_ENV", "sandbox")"""
from shared.swift_env import SWIFT_ENV

_BASE_URLS = {
    "sandbox": "https://sandbox.swift.com/alliancecloud/v2",
    "test":    "https://api-test.swiftnet.sipn.swift.com/alliancecloud/v2",
    "prod":    "https://api.swiftnet.sipn.swift.com/alliancecloud/v2",
}

BASE_URL = _BASE_URLS[SWIFT_ENV]


# ── HTTP helper ───────────────────────────────────────────────────────────

def _swift_request(
    token: str,
    path: str,
    method: str = "GET",
    body: Optional[dict] = None,
    max_retries: int = 3,
) -> tuple[dict, Optional[str]]:
    """
    Realiza una petición a Swift con retry + exponential backoff.

    Returns:
        (data_dict, integrity_header)
    """
    url = path if path.startswith("http") else f"{BASE_URL}{path}"

    for attempt in range(max_retries + 1):
        body_bytes = json.dumps(body).encode() if body is not None else None

        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
                "Accept":        "application/json",
            },
            method=method,
        )

        try:
            with urllib.request.urlopen(req) as resp:
                integrity = resp.headers.get("X-SWIFT-Integrity")
                data      = json.loads(resp.read())
                return data, integrity

        except urllib.error.HTTPError as e:
            try:
                error_body = json.loads(e.read())
            except Exception:
                error_body = {}

            severity    = error_body.get("severity", "")
            is_transient = e.code == 429 or (e.code >= 500 and severity == "Transient")

            if is_transient and attempt < max_retries:
                delay = 0.5 * (2 ** attempt)
                logger.warning(
                    "Swift API transient error (attempt %d), retry in %.1fs",
                    attempt + 1, delay,
                )
                time.sleep(delay)
                continue

            err = RuntimeError(
                f"Swift API error {e.code}: {error_body.get('text', 'unknown')}"
            )
            err.status_code = e.code
            err.swift_code  = error_body.get("code")
            err.severity    = severity
            raise err

    raise RuntimeError("Max retries exceeded")


# ── API pública ───────────────────────────────────────────────────────────

def list_distributions(token: str) -> list[dict]:
    """
    GET /distributions — paginado con rel:next.
    Devuelve todas las distribuciones pendientes.
    """
    page_size = 100
    offset    = 0
    all_dists = []

    while True:
        data, _ = _swift_request(
            token, f"/distributions?limit={page_size}&offset={offset}"
        )
        dists = data.get("distributions", [])
        all_dists.extend(dists)

        links    = data.get("links", [])
        has_next = any(lnk.get("rel") == "next" for lnk in links)
        if not has_next or len(dists) < page_size:
            break
        offset += page_size

    return all_dists


def _resolve_download_path(dist_id: int, service: str, dist_type: str) -> tuple[str, str]:
    """Construye el path de descarga si la distribución no trae links."""
    _paths = {
        "fin": {
            "message":              f"/fin/messages/{dist_id}",
            "transmissionReport":   f"/fin/transmission-reports/{dist_id}",
            "deliveryReport":       f"/fin/delivery-reports/{dist_id}",
            "deliveryNotification": f"/fin/delivery-notifications/{dist_id}",
            "archive":              f"/fin/archives/{dist_id}",
            "export":               f"/fin/exports/{dist_id}",
        },
        "interAct": {
            "message":              f"/interact/messages/{dist_id}",
            "transmissionReport":   f"/interact/transmission-reports/{dist_id}",
            "deliveryReport":       f"/interact/delivery-reports/{dist_id}",
            "deliveryNotification": f"/interact/delivery-notifications/{dist_id}",
            "archive":              f"/interact/archives/{dist_id}",
            "export":               f"/interact/exports/{dist_id}",
        },
        "fileAct": {
            "message":       f"/fileact/messages/{dist_id}",
            "deliveryReport":f"/fileact/delivery-reports/{dist_id}",
            "archive":       f"/fileact/archives/{dist_id}",
            "export":        f"/fileact/exports/{dist_id}",
        },
    }
    path = _paths.get(service, {}).get(dist_type)
    if not path:
        raise ValueError(f"Unknown distribution service/type: {service}/{dist_type}")

    method = "POST" if service == "fileAct" else "GET"
    return path, method


def download_distribution(token: str, distribution: dict) -> dict:
    """
    Descarga el payload de una distribución.
    Usa el link rel:download si está disponible.

    Returns:
        dict con payload, integrity, distribution_id, service, dist_type
    """
    dist_id   = distribution["id"]
    service   = distribution["service"]
    dist_type = distribution["type"]
    links     = distribution.get("links", [])

    download_link = next(
        (lnk["href"] for lnk in links if lnk.get("rel") == "download"), None
    )

    if download_link:
        # Extraer path de la URL completa
        from urllib.parse import urlparse
        path   = urlparse(download_link).path
        method = "POST" if service == "fileAct" else "GET"
    else:
        path, method = _resolve_download_path(dist_id, service, dist_type)

    body = None
    if service == "fileAct" and method == "POST":
        callback = os.environ.get("FILEACT_CALLBACK_URL", "")
        body = {"callback_url": callback}

    logger.info(f"download_distribution: path={path} method={method}")
    data, integrity = _swift_request(token, path, method=method, body=body)
    return {
        "payload":         data,
        "integrity":       integrity,
        "distribution_id": dist_id,
        "service":         service,
        "dist_type":       dist_type,
    }


def ack_distribution(token: str, distribution_id: int) -> None:
    """ACK — remueve la distribución de la lista pendiente."""
    _swift_request(
        token, f"/distributions/{distribution_id}/acks", method="POST", body={}
    )
    logger.info("ACK sent for distribution %s", distribution_id)


def nak_distribution(token: str, distribution_id: int, reason: str = "Processing error") -> None:
    """NAK — la distribución vuelve al siguiente polling."""
    _swift_request(
        token,
        f"/distributions/{distribution_id}/naks",
        method="POST",
        body={"message": reason[:200]},
    )
    logger.info("NAK sent for distribution %s: %s", distribution_id, reason)


def batch_update_distributions(token: str, updates: list[dict]) -> dict:
    """PATCH /distributions — batch ACK/NAK (hasta 200)."""
    data, _ = _swift_request(
        token, "/distributions", method="PATCH", body={"distributions": updates}
    )
    return data
