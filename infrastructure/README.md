# Infraestructura — Swift SQS Connector
 
Acá vive la infraestructura como código (AWS CloudFormation) del conector entre
**Swift Alliance Cloud** y el **core bancario**. En pocas palabras, este conector
se encarga de recibir los mensajes que llegan desde Swift y llevarlos al core, de
enviar hacia Swift los que produce el core, y de mantener vivo el token de
autenticación para que todo eso funcione sin intervención manual.
 
> **Ojo:** el repositorio todavía se está armando. La parte de los stacks y sus
> dependencias ya está clara y estable, pero el cómo se despliega (el pipeline de
> Azure DevOps), los scripts de apoyo y alguno que otro detalle lo estamos definiendo. Al final esta la sección [Estado del proyecto](#estado-del-proyecto)
> con el resumen de qué está listo y qué no.
 
---
 
## Cómo está organizado
 
```
infrastructure/
├── stacks/          Las plantillas CloudFormation, una por stack (orden 01→07)
├── parameters/      Los valores por ambiente (dev / qa / prod) — en construcción
└── scripts/         Scripts de despliegue y utilidades — en construcción
```
 
La numeración de los stacks (01 al 07) **marca el orden en que se despliegan**: los stacks se pasan información entre sí a través de *Exports*
de CloudFormation. Cada uno importa valores del anterior con `Fn::ImportValue`,
así que si se despliegan en otro orden, alguno va a fallar porque el dato que
necesita todavía no existe.
 
---
 
## Los 7 stacks
 
| Orden | Archivo | Depende de | Qué crea |
|-------|---------|------------|----------|
| 1 | `01-kms.yaml` | — | La clave KMS con rotación anual (cifra credenciales y token) |
| 2 | `02-dynamodb.yaml` | 01 | La tabla DynamoDB cifrada donde viven las credenciales y el token |
| 3 | `03-sqs.yaml` | — | Las colas SQS: la DLQ y la de resultados de envío |
| 4 | `04-iam.yaml` | 01, 02, 03 | El rol de las Lambdas, con permisos mínimos |
| 5 | `05-lambdas.yaml` | 01, 02, 03, 04 | Las 4 Lambdas del conector, sus logs y la policy de invocación |
| 6 | `06-eventbridge.yaml` | 05 | Los schedules que disparan al token-refresher y al message-poller |
| 7 | `07-triggers.yaml` | 03, 05 | El disparador SQS→message-sender y la config de reintentos del dispatcher |
 
Un detalle útil: los stacks **01 (KMS)** y **03 (SQS)** no dependen de nadie, así
que podrían ir en paralelo si hiciera falta. Del 04 en adelante sí hay que ir en
orden, uno tras otro.
 
---
 
## Qué hace cada pieza
 
Las credenciales y el token se guardan en **DynamoDB cifrado con KMS** (no en
Secrets Manager). La tabla usa
`pk="credentials"` para el consumer key y sus campos cifrados, y `pk="token"`
para el access token junto con su vigencia.

El código de las Lambdas vive en `lambdas/` (Python); los stacks de aquí definen su infraestructura en AWS.

Y estas son las cuatro Lambdas que hacen el trabajo (todas viven en
`05-lambdas.yaml`):
 
- **token-refresher** — pide un token fresco a Swift y lo deja guardado en DynamoDB.
- **message-poller** — le pregunta a la Swift Messaging API (`GET /distributions`)
  qué mensajes hay disponibles y se los pasa al dispatcher.
- **message-dispatcher** — agarra cada mensaje que entra, lo valida y decide a
  dónde va (las colas del core, los reportes o la cuarentena).
- **message-sender** — toma lo que el core deja en su cola de salida, lo firma y
  lo manda a Swift.
---
 
## Cómo fluye todo
 
### Cuando llega algo desde Swift (Swift → core)
 
1. **EventBridge** despierta al **message-poller** cada tantos minutos.
2. El poller le pregunta a Swift, con `GET /distributions`, qué hay disponible.
3. Por cada mensaje, le pasa la posta al **message-dispatcher**.
4. El dispatcher descarga el contenido, lo valida y lo manda a donde corresponda:
   la cola del core, los reportes o la cuarentena si algo no cuadra.
### Cuando el core quiere mandar algo (core → Swift)
 
1. El core deja un mensaje MT/MX en su cola de salida.
2. Eso dispara automáticamente al **message-sender** (configurado en
   `07-triggers.yaml`).
3. El sender arma la firma que Swift exige y envía el mensaje.
4. El resultado queda registrado en la cola de resultados, para saber cómo fue.
### El token, que hace que todo lo anterior funcione
 
1. **EventBridge** despierta al **token-refresher** según el intervalo que está en
   `06-eventbridge.yaml` (`rate(50 minutes)`).
2. El refresher consigue un token nuevo de Swift (con el flujo JWT Bearer) y lo
   guarda cifrado en DynamoDB.
3. El poller, el dispatcher y el sender leen ese token de DynamoDB cada vez que
   necesitan hablar con Swift.
> **Un pendiente menor:** el token de Swift dura alrededor de 30 minutos, y hoy el
> refresher está en `rate(50 minutes)`. Esto lo validamos con el equipo si
> ese intervalo es el que queremos o conviene acortarlo, para que nunca quede una
> ventana sin token vigente.
 
---
 
## Cómo se despliega
 
> **Todavía en definición.** La idea es desplegar con un **pipeline de Azure
> DevOps**, que aún se está armando. Sea cual sea la herramienta final, lo
> importante es que **respete el orden 01→07**, porque cada stack necesita los
> exports del anterior.
 
Todos los stacks reciben el parámetro `Environment` (`dev`, `qa` o `prod`) y
nombran sus recursos con ese sufijo — por ejemplo `swift-sqs-dev-kms-key-arn`.
 
Por convención (según lo que dice cada plantilla), los stacks se nombran con el
patrón `swift-sqs-{env}-{stack}`, como `swift-sqs-dev-kms`.
 
---
 
## Estado del proyecto
 
**Lo que ya está consolidado:**
 
- La organización de las carpetas.
- Las 7 plantillas y el orden en que se despliegan.
- Cómo dependen unas de otras (los `Fn::ImportValue`).
- Que el almacenamiento va en DynamoDB cifrado con KMS.
**Lo que todavía se está definiendo:**

- El pipeline de despliegue en Azure DevOps (los stacks 05-07 esperan el empaquetado del código Python de las Lambdas).
- Los archivos de parámetros por ambiente (`parameters/`).
- El intervalo fino del token-refresher (lo del pendiente de más arriba).
---
 
## Un par de notas técnicas
 
- **Sobre la dependencia entre IAM y las Lambdas:** el rol necesita permiso para
  invocar al dispatcher, pero el dispatcher todavía no existe cuando se crea el
  rol. Para evitar ese círculo, la politica de invocación se puso en el stack de
  Lambdas (05) en vez del de IAM (04).
- **Permisos justos:** el rol da solo lo necesario sobre los recursos concretos
  (la tabla, la clave KMS y las colas), nada de permisos de más.
- **Por si algo falla:** las colas tienen DLQ con reintentos, el dispatcher
  reintenta de forma asíncrona y manda a la DLQ lo que no logra procesar, y el
  sender reporta los fallos mensaje por mensaje dentro de un lote.
 