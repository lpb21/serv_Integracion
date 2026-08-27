"""
queue_router.py
Enruta distribuciones Swift a las colas SQS del core bancario.

Colas del core (ya existen, el conector NO las crea):
  SQS_CORE_MT_INBOUND_URL  -> mensajes FIN (fin)
  SQS_CORE_MX_INBOUND_URL  -> mensajes MX (interAct)
  SQS_CORE_FILEACT_URL     -> FileAct (opcional - pendiente)

El body publicado en las colas del core es el payload Swift
decodificado de base64 a TEXTO PLANO - sin wrapper JSON.
"""

import base64
import json
import os

SERVICE_TO_ENV = {
    'fin':      'SQS_CORE_MT_INBOUND_URL',
    'interAct': 'SQS_CORE_MX_INBOUND_URL',
    'fileAct':  'SQS_CORE_FILEACT_URL',
}


def resolve_queue_url(service: str) -> str:
    """Resuelve la URL de la cola del core para un servicio Swift."""
    env_var = SERVICE_TO_ENV.get(service)
    if not env_var:
        raise ValueError(f'Unknown Swift service: {service}')

    url = os.environ.get(env_var, '')

    if not url:
        if service == 'fileAct':
            raise ValueError(
                'FileAct queue not configured (SQS_CORE_FILEACT_URL is empty). '
                'Set this env var when the core defines the FileAct queue.'
            )
        raise ValueError(f"Queue URL not configured for service '{service}' (env: {env_var})")

    return url


def build_sqs_message(downloaded: dict) -> dict:
    """
    Construye el mensaje SQS para publicar en la cola del core.

    Body: payload Swift decodificado de base64 a texto plano.
      FIN:      texto MT (bloque 4)
      InterAct: XML MX completo (AppHdr + Document)

    MessageAttributes: metadata para filtrado sin parsear el body.
    """
    payload         = downloaded.get('payload')
    integrity       = downloaded.get('integrity')
    distribution_id = downloaded.get('distribution_id')
    service         = downloaded.get('service')
    dist_type       = downloaded.get('type')

    # Decodificar payload de base64 a texto plano
    try:
        payload_data = payload.get('payload') if isinstance(payload, dict) else payload
        if isinstance(payload_data, str):
            raw_payload = base64.b64decode(payload_data).decode('utf-8')
        else:
            raw_payload = json.dumps(payload_data)
    except Exception:
        raw_payload = payload if isinstance(payload, str) else json.dumps(payload)

    message_attributes = {
        'distributionId': {
            'DataType':    'Number',
            'StringValue': str(distribution_id),
        },
        'service': {
            'DataType':    'String',
            'StringValue': service,
        },
        'distributionType': {
            'DataType':    'String',
            'StringValue': dist_type,
        },
        'swiftEnv': {
            'DataType':    'String',
            'StringValue': os.environ.get('SWIFT_ENV', 'sandbox'),
        },
    }

    if integrity:
        message_attributes['integrityToken'] = {
            'DataType':    'String',
            'StringValue': integrity,
        }

    return {
        'message_body':       raw_payload,
        'message_attributes': message_attributes,
    }
