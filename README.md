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
 
## Referencias
 
- Swift Messaging API — https://developer.swift.com/apis/swift-messaging-api
- Autenticación JWT-Bearer — https://docs.developer.swift.com/docs/api-guides/swift-messaging-api/authentication
- Código de ejemplo oficial — https://github.com/swiftinc/api-sample-code