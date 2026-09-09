"""Resuelve el ambiente de SWIFT a partir del ambiente de infra (dev/qa/prod)."""
import os

# Ambiente de infra: dev / qa / prod
_INFRA_ENV = os.environ.get("SWIFT_ENV", "sandbox")

# Mapeo infra → SWIFT (sandbox/test/prod)
_ENV_TO_SWIFT = {
    "dev":  "sandbox",
    "qa":   "test",
    "prod": "prod",
    "sandbox": "sandbox",
    "test":    "test",
}

# Ambiente SWIFT ya resuelto (sandbox/test/prod)
SWIFT_ENV = _ENV_TO_SWIFT.get(_INFRA_ENV, _INFRA_ENV)