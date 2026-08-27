"""
queue_router.py
Enruta distribuciones Swift a la cola SQS de entrada del core.
 
Estructura de colas (las colas ya existen, el conector NO las crea):
  Entrada (Swift -> core):
    SQS_CORE_INBOUND_URL   -> UNA sola cola FIFO para MT y MX juntos
                              (fin e interAct entran por la misma cola)
  Salida (core -> Swift): la maneja el message-sender, con dos colas
    separadas (MT y MX). No se resuelven aqui.
  FileAct: pendiente (aun no definido por el core).
 
Cola FIFO:
  Al publicar en una cola FIFO, AWS exige MessageGroupId, y para la
  deduplicacion se usa MessageDeduplicationId.
 
  - MessageGroupId: se agrupa POR SERVICIO (fin / interAct). Asi, los MT se
    procesan en orden entre si y los MX entre si, pero MT y MX pueden ir en
    paralelo. Es un balance entre orden y rendimiento: para mensajeria
    financiera el orden dentro de cada tipo importa, pero no hace falta
    serializar tipos distintos entre si.
    (Alternativas consideradas: un group id fijo -> orden total, sin
    paralelismo; group id por distribution.id -> maximo paralelismo, sin
    orden. Se eligio el punto intermedio, por servicio.)
 
  - MessageDeduplicationId: el distribution.id. Swift puede reenviar una misma
    distribucion marcada como transmission_possible_duplicate; usar el
    distribution.id como dedup id hace que la cola absorba ese duplicado
    dentro de la ventana FIFO de 5 minutos.
 
El body publicado en la cola es el payload Swift decodificado de base64 a
TEXTO PLANO - sin wrapper JSON.
"""
 
import base64
import json
import os
 
# Servicios Swift que entran al core. Todos van a la MISMA cola de entrada;
# el service solo se usa para el MessageGroupId (agrupacion FIFO) y como
# atributo del mensaje.
INBOUND_SERVICES = {'fin', 'interAct'}
 
 
def resolve_inbound_queue_url(service: str) -> str:
    """
    Resuelve la URL de la unica cola de entrada del core.
 
    MT (fin) y MX (interAct) comparten la misma cola de entrada, asi que el
    service se valida pero no cambia la cola destino.
    """
    if service not in INBOUND_SERVICES:
        # fileAct u otros aun no soportados en la entrada.
        raise ValueError(
            f"Servicio Swift no soportado en la cola de entrada: '{service}'. "
            f"Soportados: {sorted(INBOUND_SERVICES)}."
        )
 
    url = os.environ.get('SQS_CORE_INBOUND_URL', '')
    if not url:
        raise ValueError(
            "Cola de entrada del core no configurada "
            "(SQS_CORE_INBOUND_URL vacio)."
        )
 
    return url
 
 
def build_sqs_message(downloaded: dict) -> dict:
    """
    Construye el mensaje SQS para publicar en la cola de entrada del core.
 
    Body: payload Swift decodificado de base64 a texto plano.
      FIN:      texto MT (bloque 4)
      InterAct: XML MX completo (AppHdr + Document)
 
    Devuelve tambien los campos FIFO (group_id y deduplication_id) para que el
    dispatcher los pase al send_message.
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
        # Campos FIFO:
        'message_group_id':        service,                  # Opcion 2: por servicio
        'message_deduplication_id': str(distribution_id),    # dedup por distribution.id
    }