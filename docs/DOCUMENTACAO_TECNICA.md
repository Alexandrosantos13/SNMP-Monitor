# Documentação técnica

## Arquitetura

```
 MikroTik ──SNMP(UDP/161)──▶ snmp_client ──▶ collector (thread única) ──▶ storage (SQLite)
                                                   │                          ▲
                                                   ├──▶ alerts (pop-up/e-mail)│
                                                   ▼                          │
                                    buffer em memória ──▶ Flask API ──▶ navegador (Chart.js)
```

- **Coleta** (`collector.py`): uma thread daemon; o botão Iniciar é idempotente (reutiliza a thread). Pausa apenas suspende o laço; Resetar limpa o buffer em memória e incrementa o id de sessão (a interface limpa os gráficos ao ver a nova sessão).
- **Armazenamento** (`storage.py`): SQLite com `samples(ts, in_bps, out_bps, cpu)` e `events(ts, kind, level, message)`. Persistência em `data/monitor.db`.
- **Interface**: página única que consulta `/api/live` a cada 1 s (amostras novas + alertas novos + status). Histórico via `/api/history?start&end` (epoch). Para períodos longos (> 1500 amostras) a consulta devolve médias por faixa de tempo, apenas para exibição; o total real é informado.
- **Bibliotecas**: Flask, pysnmp (>= 7.1), cryptography (SNMPv3 AES/DES), Chart.js 4 (arquivo local em `static/vendor`, funciona sem internet), smtplib da biblioteca padrão.

## MIBs e OIDs

| Informação | MIB::objeto | OID |
|---|---|---|
| Nome da interface | IF-MIB::ifName | 1.3.6.1.2.1.31.1.1.1.1.*idx* |
| Bytes recebidos (64 bits) | IF-MIB::ifHCInOctets | 1.3.6.1.2.1.31.1.1.1.6.*idx* |
| Bytes enviados (64 bits) | IF-MIB::ifHCOutOctets | 1.3.6.1.2.1.31.1.1.1.10.*idx* |
| Fallback 32 bits | ifInOctets / ifOutOctets | 1.3.6.1.2.1.2.2.1.10 / .16 |
| Velocidade nominal | IF-MIB::ifHighSpeed | 1.3.6.1.2.1.31.1.1.1.15.*idx* |
| Uptime (detecta reboot) | SNMPv2-MIB::sysUpTime | 1.3.6.1.2.1.1.3.0 |
| CPU | HOST-RESOURCES-MIB::hrProcessorLoad | 1.3.6.1.2.1.25.3.3.1.2.*n* |

A interface é localizada pelo **nome** (ex.: `ether1`) percorrendo `ifName`; o índice é guardado e o nome é reconferido a cada leitura. Se o roteador devolver vários `hrProcessorLoad` (um por núcleo), a CPU exibida é a **média**.

## Cálculo do tráfego (bps)

```
bps = (bytes_atual − bytes_anterior) × 8 / Δt
```
`Δt` é o intervalo **real** medido com relógio monotônico entre as respostas (não o intervalo configurado). Tratamentos:

- Counter64 que diminui → contador reiniciado: amostra de tráfego descartada (nunca gera taxa negativa) e evento registrado.
- Counter32 (fallback) que diminui → tratado como *wrap-around* (`+ 2³²`).
- Taxa maior que 110 % da velocidade da interface (`ifHighSpeed`; 10 Gbps se desconhecida) → descartada como absurda.
- `sysUpTime` menor que o anterior → roteador reiniciou: baseline reiniciada.
- Nome da interface diferente do esperado → índice reconsultado, baseline reiniciada.
- Após erro de comunicação, pausa/retomada ou reset: baseline reiniciada (evita média sobre o intervalo sem coleta). A primeira leitura só tem CPU (tráfego precisa de duas leituras).

## Estratégia de alertas

Limites separados: **IN**, **OUT** (bps) e **CPU** (%); `0` desativa. Comparação por métrica com `valor > limite`.

- Notifica na transição *normal → excedido*.
- Enquanto continua excedido, repete só após *Repetir alerta a cada (s)* (padrão 300; `0` = nunca repete).
- Ao voltar ao normal, o estado é rearmado e a recuperação é registrada em Eventos (sem notificação).
- Pop-up: janela modal na interface com métrica, valor, limite e horário (fila se vários). E-mail: mesma informação, enviado em thread separada com timeout de 15 s.
- Falha de e-mail → evento `email_error` + aviso na tela; a coleta não é afetada.

## Tratamento de erros

Timeout/sem conectividade, falha de autenticação v3, OID não suportado, interface inexistente e erros inesperados são capturados no laço de coleta: a faixa vermelha mostra a mensagem, a coleta continua tentando e a recuperação é registrada. Eventos de erro só são gravados quando a mensagem muda (sem inundar o log).

## Decisões e limitações

- **Reset** limpa só a série da sessão em tempo real; o histórico no SQLite não é apagado.
- **Segurança**: prefira SNMPv3; em v2c use community de leitura restrita por `addresses=`. `config.json` fica com permissão 600 e fora do Git. Senhas nunca são enviadas de volta à interface (aparecem mascaradas).
- A interface é servida sem autenticação: rode em `127.0.0.1` (padrão) ou em rede confiável.
- Pop-up é exibido pela página aberta no navegador (não há notificação do sistema operacional quando a página está fechada; o e-mail cobre esse caso).
