# Swift SQS Connector
 
Conector serverless en AWS entre la **Swift Messaging API v2.1.0** (Alliance
Cloud) y el **core bancario**, comunicándose por colas SQS. En pocas palabras:
recibe los mensajes que llegan desde Swift y los lleva al core, envía hacia Swift
los que produce el core, y mantiene vivo el token de autenticación para que todo
eso ocurra sin intervención manual.
 
Está hecho en **Python 3.12** (boto3 + cryptography) sobre AWS Lambda, y la
infraestructura se define con CloudFormation.
 
> **Ojo:** el repositorio todavía se está armando. La estructura y la
> infraestructura ya están claras; el pipeline de despliegue y algunos detalles
> siguen en definición. El README de `infrastructure/` tiene el estado detallado
> de esa capa.
 
---
 
## Cómo funciona
 
El conector son cuatro funciones Lambda, cada una con su responsabilidad:
 
- **token-refresher** — se dispara por EventBridge cada cierto tiempo. Hace el
  flujo OAuth2 JWT-Bearer contra Swift y guarda el token cifrado (con KMS) en
  DynamoDB.
- **message-poller** — cada pocos minutos le pregunta a Swift con
  `GET /distributions` qué mensajes hay disponibles, y por cada uno invoca al
  dispatcher de forma asíncrona.
- **message-dispatcher** — descarga el contenido del mensaje, lo decodifica, y lo
  publica en la cola del core que corresponda (o lo manda a cuarentena si algo no
  cuadra), y le confirma a Swift la recepción.
- **message-sender** — se dispara cuando el core deja un mensaje en su cola de
  salida. Detecta si es MT o MX, lo parsea, lo firma con `X-SWIFT-Signature` y lo
  envía a Swift, dejando el resultado en la cola de resultados.
Las credenciales de Swift y el Bearer token se guardan en **DynamoDB cifrado con
KMS** (una sola tabla, con los campos sensibles cifrados). No se usa Secrets
Manager.
 
---
 
## Cómo está organizado el repo
 
```
.
├── infrastructure/
│   ├── stacks/          Los 7 stacks CloudFormation (orden 01→07)
│   ├── parameters/      Valores por ambiente — en construcción
│   ├── scripts/         Utilidades de infraestructura
│   └── README.md        Detalle de la infraestructura y el despliegue
├── lambdas/
│   ├── token_refresher/handler.py
│   ├── message_poller/handler.py
│   ├── message_dispatcher/handler.py
│   └── message_sender/handler.py
├── shared/              Código común que usan las Lambdas
│   ├── swift_store.py            DynamoDB + KMS (credenciales y token)
│   ├── swift_auth.py             JWT RS256 + obtención del token OAuth2
│   ├── swift_client.py           Inbound (distributions, download, ACK)
│   ├── swift_client_outbound.py  Outbound (POST de mensajes)
│   ├── swift_signature.py        Construcción de X-SWIFT-Signature
│   ├── mt_parser.py              Parser de mensajes MT (5 bloques)
│   ├── mx_parser.py              Parser de mensajes MX (AppHdr + Document)
│   └── queue_router.py           Enrutamiento de servicio → cola SQS
├── scripts/             Scripts de apoyo (build, setup, pruebas locales)
├── tests/
│   ├── unit/            Pruebas unitarias (parsers, sin dependencias)
│   └── sandbox/         Pruebas de integración contra el sandbox real
├── azure-pipelines.yml  Pipeline de despliegue de la infraestructura
└── requirements.txt     Dependencias de producción (boto3, cryptography)
```
 
Los stacks de infraestructura viven en `infrastructure/stacks/` y tienen su propio
README con el detalle del despliegue y las dependencias entre ellos.
 
---
 
## Setup local
 
Para desarrollar y probar en tu máquina (WSL2 recomendado):
 
```bash
# 1. Entorno virtual e instalación de dependencias
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
 
# 2. Configurar credenciales locales
cp .env.example .env
# editar .env con tu SWIFT_CONSUMER_KEY y SWIFT_CONSUMER_SECRET
 
# 3. Levantar recursos AWS locales (LocalStack)
python scripts/setup_local.py
 
# 4. Cargar las credenciales de Swift en el store local
python scripts/setup_credentials.py
 
# 5. Obtener el primer token
python -m lambdas.token_refresher.handler
 
# 6. Correr las pruebas
python tests/unit/test_parsers.py
python scripts/run_sender_local.py
python scripts/run_dispatcher_local.py
```
 
El `.env` con tus credenciales nunca se commitea (está en `.gitignore`). La
plantilla `.env.example` sí, para que se sepa qué variables hacen falta.
 
Un detalle sobre los ambientes: `SWIFT_ENV` (sandbox / test / prod) define contra
qué endpoint de la **API de Swift** te conectas, y es distinto del ambiente de
despliegue en AWS (dev / qa / prod). Para desarrollo local se usa
`SWIFT_ENV=sandbox`.
 
---
 
## Despliegue
 
La infraestructura se despliega con un **pipeline de Azure DevOps**
(`azure-pipelines.yml`), que crea los stacks de CloudFormation en orden. El
detalle completo del despliegue, las dependencias entre stacks y el estado actual
están en `infrastructure/README.md`.
 
---

## Casos de uso y cómo probarlos

El conector tiene dos sentidos. Las pruebas de abajo se hacen en **dev** (Swift sandbox) desde AWS CloudShell o con la CLI de AWS.

| # | Caso de uso | Qué debe pasar |
| --- | --- | --- |
| 1 | Token | Cada 25 min el token-refresher obtiene el Bearer token (`expires_in=1799` segundos, unos 30 min) y lo guarda cifrado en DynamoDB |
| 2 | Entrada | El poller consulta a Swift cada 2 min, el dispatcher descarga cada mensaje, lo publica en la cola FIFO de entrada del core y envía el ACK |
| 3 | Salida MT | Un MT en la cola de salida MT se parsea, se firma y Swift lo acepta (`fin.103`) |
| 4 | Salida MX | Un MX en la cola de salida MX se parsea, se firma y Swift lo acepta (por ejemplo `pacs.008.001.08`) |
| 5 | Resultado al core | Cada envío deja un mensaje `Accepted` o `Failed` en la cola `send-results` |

### Credenciales (una vez por ambiente)

Las credenciales viven en la tabla DynamoDB `swift-sqs-<env>-store`, con el secreto cifrado con KMS. Se cargan con `scripts/setup_credentials.py`. En sandbox, la llave y el certificado de firma son el par público de prueba de Swift.

### Probar la entrada

1. Invocar el poller a mano:
   ```bash
   aws lambda invoke --function-name swift-sqs-dev-message-poller --region us-east-1 /tmp/out.json && cat /tmp/out.json
   ```
   Debe responder `{"statusCode": 200, "dispatched": N, "failed": 0}`.
2. Revisar el log de `/aws/lambda/swift-sqs-dev-message-dispatcher`. Por cada distribución debe aparecer `published to SQS` y luego `ACK sent`.
3. Ver los mensajes en la cola de entrada, sin consumirlos:
   ```bash
   Q=$(aws sqs get-queue-url --queue-name dev-swift-queue.fifo --query QueueUrl --output text --region us-east-1)
   aws sqs get-queue-attributes --queue-url $Q --attribute-names ApproximateNumberOfMessages --region us-east-1
   aws sqs receive-message --queue-url $Q --visibility-timeout 0 --max-number-of-messages 1 --region us-east-1
   ```

El sandbox de Swift devuelve siempre las mismas 3 distribuciones aunque se envíe el ACK. Es el comportamiento esperado del simulador. La cola FIFO deduplica por `distribution.id` en una ventana de 5 minutos.

### Probar la salida

1. Enviar un mensaje de prueba (datos ficticios, referencia nueva en cada envío):
   ```bash
   python scripts/send_test_message.py mt
   python scripts/send_test_message.py mx
   python scripts/send_test_message.py mx --file mi_mensaje.xml
   ```
2. Revisar el log de `/aws/lambda/swift-sqs-dev-message-sender`:
   - `message-sender: parsed` indica el formato, el servicio y el tipo de mensaje.
   - `message-sender: sent` con un `message_cloud_reference` indica que Swift lo aceptó.
   - `message-sender: error` incluye el código de Swift (`swiftCode`) y el texto. Los 4xx no se reintentan.
3. Ver el resultado en la cola `swift-sqs-dev-send-results.fifo` (Enviar y recibir mensajes → Sondear). Debe haber un `Accepted`, o un `Failed` con `error_code` y `error_text`.

El sender acepta el MX con `AppHdr` y `Document`, o dentro de un `DataPDU`. Para InterAct arma el cuerpo con `service_code`, `requestor` y `responder`:
- `service_code`: variable `SWIFT_INTERACT_SERVICE_CODE` (parámetro `InterActServiceCode` del stack 06).
- `requestor` y `responder`: DN `ou=<SWIFT_DN_OU>,o=<bic8>,o=swift`, derivados de los BIC del `AppHdr`.
- `network_info.request_type` y `request_subtype`: salen de `MsgDefIdr` y `BizSvc` del `AppHdr`.

### Errores habituales

| Síntoma | Causa probable |
| --- | --- |
| `400 SwAP09` | El mensaje salió sin firma: la llave o el certificado en DynamoDB son el placeholder |
| `400 SwAP504` | El cuerpo no cumple el esquema de Swift: revisar los campos que menciona el error |
| `401` | Token vencido o credenciales distintas a las de la app del Developer Portal |
| `Credentials not found in DynamoDB` | Falta correr `scripts/setup_credentials.py` en ese ambiente |

### Pruebas locales

```bash
python tests/unit/test_parsers.py
```
Los scripts `scripts/run_dispatcher_local.py` y `scripts/run_sender_local.py` ejecutan los handlers en local contra LocalStack.
 
## Referencias
 
- Swift Messaging API — https://developer.swift.com/apis/swift-messaging-api
- Autenticación JWT-Bearer — https://docs.developer.swift.com/docs/api-guides/swift-messaging-api/authentication
- Código de ejemplo oficial — https://github.com/swiftinc/api-sample-code