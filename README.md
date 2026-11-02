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
 
El conector funciona en dos sentidos: **entrada** (Swift hacia el core) y **salida** (core hacia Swift). Esta sección explica cada caso de uso y cómo probarlo en **dev**, que usa Swift sandbox. Los comandos se ejecutan desde AWS CloudShell o desde una terminal con la CLI de AWS y credenciales de la cuenta de dev.
 
Estado: los cinco casos de uso están validados en dev contra Swift sandbox. En test y producción hay que repetir las pruebas con las credenciales y llaves reales de cada ambiente.
 
### Casos de uso
 
| # | Caso de uso | Qué debe pasar |
| --- | --- | --- |
| 1 | Token | Cada 25 minutos el token-refresher obtiene el Bearer token de Swift y lo guarda cifrado en DynamoDB. `expires_in=1799` son segundos, unos 30 minutos |
| 2 | Entrada | Cada 2 minutos el poller consulta a Swift, el dispatcher descarga cada mensaje, lo publica en la cola FIFO de entrada del core y envía el ACK a Swift |
| 3 | Salida MT | Un mensaje MT en la cola de salida MT se parsea, se firma y Swift lo acepta (por ejemplo `fin.103`) |
| 4 | Salida MX | Un mensaje MX en la cola de salida MX se parsea, se firma y Swift lo acepta (por ejemplo `pacs.008.001.08`) |
| 5 | Resultado al core | Cada envío deja un mensaje `Accepted` o `Failed` en la cola `send-results`, para que el core lo correlacione |
 
### Antes de probar: credenciales
 
Las credenciales de Swift viven en la tabla DynamoDB `swift-sqs-<env>-store`. El `consumer_key` va en claro y el `consumer_secret`, la llave privada y el certificado van cifrados con KMS. Se cargan una vez por ambiente con `scripts/setup_credentials.py`.
 
En sandbox, la llave y el certificado de firma son el par público de prueba que publica Swift. En test y producción son las llaves institucionales que entrega Seguridad. Si la llave guardada es el valor `SANDBOX_PLACEHOLDER`, el sender envía sin firma y Swift responde `400 SwAP09`.
 
Para confirmar que las credenciales existen y cuándo se cargaron, sin leer ningún secreto:
 
```bash
aws dynamodb get-item --table-name swift-sqs-dev-store \
  --key '{"pk":{"S":"credentials"}}' \
  --projection-expression "pk, consumer_key, updated_at" --region us-east-1
```
 
### Probar el token
 
```bash
aws lambda invoke --function-name swift-sqs-dev-token-refresher --region us-east-1 /tmp/out.json
```
 
En el log de `/aws/lambda/swift-sqs-dev-token-refresher` debe aparecer `token obtained, expires_in=1799` y luego `token saved to DynamoDB`.
 
### Probar la entrada
 
1. Invocar el poller a mano:
```bash
   aws lambda invoke --function-name swift-sqs-dev-message-poller --region us-east-1 /tmp/out.json && cat /tmp/out.json
```
 
   Debe responder `{"statusCode": 200, "dispatched": N, "failed": 0}`. Si no hay distribuciones pendientes, responde `No distributions available`.
 
2. Revisar el log de `/aws/lambda/swift-sqs-dev-message-dispatcher`. Por cada distribución debe aparecer, en este orden:
   - `message-dispatcher: processing id=... service=... type=...`
   - `message-dispatcher: published to SQS id=... messageId=...`
   - `ACK sent for distribution ...`
3. Ver los mensajes en la cola de entrada del core, sin consumirlos:
```bash
   Q=$(aws sqs get-queue-url --queue-name dev-swift-queue.fifo --query QueueUrl --output text --region us-east-1)
   aws sqs get-queue-attributes --queue-url $Q --attribute-names ApproximateNumberOfMessages --region us-east-1
   aws sqs receive-message --queue-url $Q --visibility-timeout 0 --max-number-of-messages 1 --region us-east-1
```
 
   Con `--visibility-timeout 0` el mensaje vuelve a quedar visible de inmediato, así la prueba no lo consume.
 
Notas sobre el sandbox:
- Swift sandbox es un simulador y devuelve siempre las mismas 3 distribuciones, aunque se envíe el ACK. Es el comportamiento esperado.
- La cola FIFO de entrada deduplica por `distribution.id` en una ventana de 5 minutos, así que las repeticiones del sandbox no se acumulan dentro de esa ventana.
### Probar la salida
 
La salida se prueba con `scripts/send_test_message.py`.
 
**Qué hace.** Publica un mensaje de prueba en la cola de salida del core, lo que dispara al `message-sender`. Evita armar el mensaje a mano. Sirve para MT y para MX.
 
- Elige la cola según el formato: `dev-swift-output-queue.fifo` para MT y `dev-swift-output-mx-queue.fifo` para MX.
- Genera una referencia nueva en cada envío (`TEST-<timestamp>` o `MXTEST-<timestamp>`) y un UETR nuevo. Swift rechaza referencias repetidas.
- Usa datos ficticios: ordenante y beneficiario de prueba, cuentas `0000000001` y `0000000002`.
- Con `--file` envía el mensaje de un archivo propio, en lugar del ejemplo.
- No lee ni usa credenciales de Swift; solo usa los permisos AWS de la sesión.
**Requisitos.** Python 3 con `boto3` (CloudShell ya lo trae) y permisos `sqs:GetQueueUrl` y `sqs:SendMessage` sobre la cola. Las Lambdas deben estar desplegadas con el código actual.
 
**Cómo ejecutarlo.**
 
```bash
python3 scripts/send_test_message.py mt
python3 scripts/send_test_message.py mx
python3 scripts/send_test_message.py mx --file mi_mensaje.xml
```
 
Opciones, todas opcionales:
 
| Opción | Uso |
| --- | --- |
| `--env dev` | Prefijo de las colas dummy. Por defecto `dev` |
| `--queue-name NOMBRE` | Otra cola, por ejemplo la real del core en qa |
| `--region us-east-1` | Región de AWS. Por defecto `us-east-1` |
| `--file RUTA` | Archivo con el mensaje a enviar, en lugar del ejemplo |
 
**Salida esperada en la terminal.**
 
```
Enviado a dev-swift-output-queue.fifo
Referencia: TEST-1793600000
MessageId:  <un UUID>
Revisa el log de swift-sqs-<env>-message-sender y la cola send-results.
```
 
La referencia lleva el timestamp del envío, así que cambia cada vez. Para MX la cola es `dev-swift-output-mx-queue.fifo` y la referencia empieza con `MXTEST-`.
 
**Qué verificar en AWS.**
 
1. Log de `/aws/lambda/swift-sqs-dev-message-sender`, unos 10 segundos después del envío:
   - `message-sender: parsed` muestra el formato (MT o MX), el servicio, el tipo de mensaje y la referencia.
   - `message-sender: sent` con un `message_cloud_reference` indica que Swift aceptó el mensaje.
   - `message-sender: error` incluye `swiftCode`, `statusCode` y el texto de Swift. Los errores 4xx no se reintentan.
2. Cola `swift-sqs-dev-send-results.fifo` (en la consola: Enviar y recibir mensajes, Sondear mensajes). Debe haber un mensaje con `"status": "Accepted"` y la misma `sender_reference` del envío. Si Swift rechazó el mensaje, el estado es `Failed` y trae `error_code` y `error_text`.
### Cómo arma el sender el mensaje a Swift
 
El sender detecta el formato por el contenido del cuerpo: un MT empieza con `{1:`, un MX con `<?xml` o con un namespace ISO 20022. Un MX puede venir con `AppHdr` y `Document`, o dentro de un `DataPDU`; en ambos casos el parser toma el `AppHdr` y el `Document`.
 
- **MT (FIN, `/fin/messages`):** el cuerpo lleva `sender_reference` (campo 108 del bloque 3), `message_type`, `sender`, `receiver`, `payload` y `network_info`.
- **MX (InterAct, `/interact/messages`):** el cuerpo no lleva `sender` ni `receiver`, y sí:
  - `service_code`: variable de entorno `SWIFT_INTERACT_SERVICE_CODE`, que viene del parámetro `InterActServiceCode` del stack 06.
  - `requestor` y `responder`: DN con la forma `ou=<SWIFT_DN_OU>,o=<bic8 en minúsculas>,o=swift`, derivados de los BIC del `AppHdr`. La unidad por defecto es `xxx`.
  - `network_info.request_type` y `network_info.request_subtype`: salen de `MsgDefIdr` y `BizSvc` del `AppHdr`.
  - `format: "MX"`.
Ambos envíos llevan el header `X-SWIFT-Signature`, un JWS RS256 que se genera en cada intento con la llave privada y el certificado guardados en DynamoDB.
 
### Errores habituales
 
| Síntoma | Causa probable |
| --- | --- |
| `400 SwAP09` | El mensaje salió sin firma: la llave o el certificado en DynamoDB son el placeholder |
| `400 SwAP504` | El cuerpo no cumple el esquema de Swift: revisar los campos que nombra el error |
| `401` | Token vencido o credenciales distintas a las de la app del Developer Portal |
| `Credentials not found in DynamoDB` | Falta correr `scripts/setup_credentials.py` en ese ambiente |
| `SWIFT_INTERACT_SERVICE_CODE is required` | El stack 06 se desplegó sin la variable del sender |
| `Failed` con `error_code: PARSE_ERROR` | El mensaje no se pudo parsear: revisar el formato del cuerpo (por ejemplo, falta el bloque 2 o 4 de un MT, o el `BizMsgIdr` de un MX) |
 
### Pruebas locales
 
```bash
python tests/unit/test_parsers.py
```
 
Los scripts `scripts/run_dispatcher_local.py` y `scripts/run_sender_local.py` ejecutan los handlers en local contra LocalStack.


## Referencias
 
- Swift Messaging API — https://developer.swift.com/apis/swift-messaging-api
- Autenticación JWT-Bearer — https://docs.developer.swift.com/docs/api-guides/swift-messaging-api/authentication
- Código de ejemplo oficial — https://github.com/swiftinc/api-sample-code