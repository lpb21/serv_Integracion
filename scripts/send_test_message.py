"""
scripts/send_test_message.py
Publica un mensaje de prueba (MT o MX) en la cola de salida del core para
disparar el message-sender y probar el flujo de SALIDA.

Uso:
  python scripts/send_test_message.py mt
  python scripts/send_test_message.py mx
  python scripts/send_test_message.py mx --file mi_mensaje.xml

Opciones:
  --env dev              prefijo de las colas dummy (dev-swift-output-queue.fifo)
  --queue-name NOMBRE    usa otra cola en lugar de la dummy
  --region us-east-1

Requiere credenciales AWS con sqs:GetQueueUrl y sqs:SendMessage sobre la cola.
Los mensajes de ejemplo usan datos ficticios. Cada envio genera una referencia
nueva (Swift rechaza referencias repetidas).
"""

import argparse
import time
import uuid
import datetime

import boto3


def build_mt(ref: str) -> str:
    return (
        '{1:F01BANKPAXXX0000000000}{2:I103BANAPAPAXXXXN}'
        '{3:{108:' + ref + '}{121:' + str(uuid.uuid4()) + '}}'
        '{4:\r\n:20:' + ref + '\r\n:23B:CRED\r\n:32A:250115USD15000,\r\n'
        ':50K:/123456789\r\nORDENANTE DE PRUEBA\r\n:59:/987654321\r\n'
        'BENEFICIARIO DE PRUEBA\r\n:71A:SHA\r\n-}'
        '{5:{CHK:ABCDEF123456}}'
    )


def build_mx(ref: str) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    ts = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    day = now.strftime('%Y-%m-%d')
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<AppHdr xmlns="urn:iso:std:iso:20022:tech:xsd:head.001.001.02">
  <Fr><FIId><FinInstnId><BICFI>BANKPAPAXXX</BICFI></FinInstnId></FIId></Fr>
  <To><FIId><FinInstnId><BICFI>BANKDEFFXXX</BICFI></FinInstnId></FIId></To>
  <BizMsgIdr>{ref}</BizMsgIdr>
  <MsgDefIdr>pacs.008.001.08</MsgDefIdr>
  <BizSvc>swift.cbprplus.03</BizSvc>
  <CreDt>{ts}</CreDt>
</AppHdr>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">
  <FIToFICstmrCdtTrf>
    <GrpHdr>
      <MsgId>{ref}</MsgId>
      <CreDtTm>{ts}</CreDtTm>
      <NbOfTxs>1</NbOfTxs>
      <SttlmInf><SttlmMtd>INDA</SttlmMtd></SttlmInf>
    </GrpHdr>
    <CdtTrfTxInf>
      <PmtId>
        <InstrId>{ref}</InstrId>
        <EndToEndId>{ref}</EndToEndId>
        <UETR>{uuid.uuid4()}</UETR>
      </PmtId>
      <IntrBkSttlmAmt Ccy="EUR">100.00</IntrBkSttlmAmt>
      <IntrBkSttlmDt>{day}</IntrBkSttlmDt>
      <ChrgBr>SHAR</ChrgBr>
      <InstgAgt><FinInstnId><BICFI>BANKPAPAXXX</BICFI></FinInstnId></InstgAgt>
      <InstdAgt><FinInstnId><BICFI>BANKDEFFXXX</BICFI></FinInstnId></InstdAgt>
      <Dbtr><Nm>ORDENANTE DE PRUEBA</Nm></Dbtr>
      <DbtrAcct><Id><Othr><Id>0000000001</Id></Othr></Id></DbtrAcct>
      <DbtrAgt><FinInstnId><BICFI>BANKPAPAXXX</BICFI></FinInstnId></DbtrAgt>
      <CdtrAgt><FinInstnId><BICFI>BANKDEFFXXX</BICFI></FinInstnId></CdtrAgt>
      <Cdtr><Nm>BENEFICIARIO DE PRUEBA</Nm></Cdtr>
      <CdtrAcct><Id><Othr><Id>0000000002</Id></Othr></Id></CdtrAcct>
      <RmtInf><Ustrd>PRUEBA {ref}</Ustrd></RmtInf>
    </CdtTrfTxInf>
  </FIToFICstmrCdtTrf>
</Document>'''


def main():
    ap = argparse.ArgumentParser(description='Envia un mensaje de prueba a la cola de salida del core')
    ap.add_argument('format', choices=['mt', 'mx'])
    ap.add_argument('--file', help='archivo con el mensaje a enviar (en vez del ejemplo)')
    ap.add_argument('--env', default='dev')
    ap.add_argument('--queue-name')
    ap.add_argument('--region', default='us-east-1')
    args = ap.parse_args()

    default_queue = {
        'mt': f'{args.env}-swift-output-queue.fifo',
        'mx': f'{args.env}-swift-output-mx-queue.fifo',
    }[args.format]
    queue_name = args.queue_name or default_queue

    prefix = 'TEST' if args.format == 'mt' else 'MXTEST'
    ref = f'{prefix}-{int(time.time())}'

    if args.file:
        with open(args.file, encoding='utf-8') as f:
            body = f.read()
        ref = f'FILE-{int(time.time())}'
    else:
        body = build_mt(ref) if args.format == 'mt' else build_mx(ref)

    sqs = boto3.client('sqs', region_name=args.region)
    url = sqs.get_queue_url(QueueName=queue_name)['QueueUrl']
    resp = sqs.send_message(
        QueueUrl=url,
        MessageBody=body,
        MessageGroupId='fin' if args.format == 'mt' else 'interAct',
        MessageDeduplicationId=str(uuid.uuid4()),
    )
    print(f'Enviado a {queue_name}')
    print(f'Referencia: {ref}')
    print(f'MessageId:  {resp["MessageId"]}')
    print('Revisa el log de swift-sqs-<env>-message-sender y la cola send-results.')


if __name__ == '__main__':
    main()