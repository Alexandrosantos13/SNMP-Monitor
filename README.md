# Monitor SNMP de Roteador (MikroTik RB750Gr3)

Aplicação web em Python que consulta um roteador via **SNMP**, mostra **tráfego IN/OUT (bps)** e **CPU (%)** em gráficos em tempo real, grava as amostras em **SQLite** para consulta histórica por período e emite **alertas por pop-up e e-mail** quando os limites configurados são ultrapassados.

Trabalho prático — Projeto e Administração de Redes.

## 1. Pré-requisitos

- Python 3.10+ (testado em 3.12)
- Um navegador moderno (Chrome, Firefox, Edge)
- Roteador MikroTik com SNMP habilitado e alcançável pela rede (seção 3)

## 2. Instalação e execução

```bash
git clone <URL-DO-SEU-REPOSITORIO>
cd snmp-monitor
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py                      # abre em http://localhost:5000
```

Opções: `python run.py --host 0.0.0.0 --port 8080` (para acessar de outra máquina da rede).

Na primeira execução não existe `config.json`: use a aba **Configurações** (ou copie `config.example.json` para `config.json`). O arquivo `config.json` e a pasta `data/` estão no `.gitignore` para não publicar credenciais nem dados.

## 3. Configuração do MikroTik (RB750Gr3 / hEX)

No terminal do RouterOS (Winbox → New Terminal, ou SSH). Ajuste a rede de origem para a do computador que roda o monitor:

```routeros
# SNMPv2c somente leitura, restrito ao host/rede do monitor
/snmp set enabled=yes
/snmp community set [find default=yes] name=monitor-lab addresses=192.168.88.0/24 read-access=yes write-access=no

# (alternativa recomendada) SNMPv3 autenticado e criptografado — o nome da community vira o usuário v3
/snmp community add name=monitor-v3 addresses=192.168.88.0/24 security=private \
    authentication-protocol=SHA1 authentication-password="SenhaAuth123" \
    encryption-protocol=AES encryption-password="SenhaCripto123" \
    read-access=yes write-access=no
```

Se houver regras de firewall bloqueando a entrada, libere UDP/161 apenas para o monitor:

```routeros
/ip firewall filter add chain=input protocol=udp dst-port=161 src-address=192.168.88.10 action=accept comment="SNMP monitor" place-before=0
```

Nomes de interface no RB750Gr3: `ether1` … `ether5`, `bridge`, etc. (use o botão **Detectar** na aplicação para listar).

Verificação rápida pelo PC (opcional, pacote `snmp`/net-snmp):

```bash
snmpwalk -v2c -c monitor-lab 192.168.88.1 1.3.6.1.2.1.31.1.1.1.1      # nomes das interfaces
snmpwalk -v2c -c monitor-lab 192.168.88.1 1.3.6.1.2.1.25.3.3.1.2      # CPU (hrProcessorLoad)
```

## 4. Configuração na aplicação

Aba **Configurações**:

| Campo | Observação |
|---|---|
| Pop-up / E-mail | marque um ou ambos |
| Limite IN / OUT | valor + unidade (bps…Gbps); aplicado **separadamente** a IN e OUT; `0` desativa |
| Limite de CPU | em %, `0` desativa |
| Repetir alerta a cada (s) | anti-repetição, ver seção 6 |
| Intervalo de coleta | segundos; zero ou negativo é rejeitado |
| E-mail | destino (formato validado) + servidor SMTP |
| Roteador e SNMP | endereço, versão (v2c/v3), community ou usuário/senhas v3, interface |

**E-mail com Gmail:** ative a verificação em duas etapas e crie uma *senha de app*; servidor `smtp.gmail.com`, porta `587`, STARTTLS, usuário = seu e-mail, senha = senha de app. A senha também pode vir da variável de ambiente `SMTP_PASSWORD`.

Use **Testar conexão SNMP** e **Enviar e-mail de teste** antes de iniciar a coleta.

## 5. Uso

- **Tempo real:** *Iniciar* / *Pausar* (preserva os dados exibidos; vira *Retomar*) / *Resetar* (limpa só os gráficos da sessão — **o histórico gravado é mantido**). Clicar em Iniciar várias vezes não cria coletas duplicadas.
- **Histórico:** escolha início e fim (ou use os atalhos) e *Aplicar período*. A tela mostra o período, o número de amostras e o horário da primeira/última; sem dados, informa isso claramente.
- **Eventos:** log de alertas, falhas de SNMP/e-mail e recuperações.

## 6. Roteiro de testes (itens da especificação)

| Teste | Como fazer | Esperado |
|---|---|---|
| Coleta SNMP | Configurar roteador, *Testar conexão*, *Iniciar* | IN/OUT/CPU atualizando a cada intervalo (gere tráfego com um download/`iperf`) |
| Controles | Iniciar ×3, Pausar, Retomar, Resetar | Uma única coleta; pausa preserva dados; reset não apaga histórico |
| Histórico | Aba Histórico, definir período | Só amostras do intervalo; mensagem se vazio |
| Alerta pop-up | Limite de CPU/tráfego baixo, pop-up ligado | Janela com métrica, valor, limite e horário |
| Alerta e-mail | E-mail ligado + destinatário | E-mail recebido, ou falha mostrada (aviso + aba Eventos) |
| Falha SNMP | Desconectar o cabo / community errada | Faixa vermelha "Falha SNMP"; app continua e recupera sozinha |
| Persistência | Fechar e reabrir `python run.py` | Amostras anteriores no Histórico |

Testes automatizados (sem roteador): `python -m unittest discover -s tests -v`.
Para demonstrar sem equipamento, marque **Modo simulação** nas configurações (apenas desenvolvimento).

## 7. Estrutura

```
run.py                 ponto de entrada
app/snmp_client.py     consultas SNMP (pysnmp), MIBs/OIDs, simulador
app/collector.py       thread de coleta, Iniciar/Pausar/Resetar, cálculo de bps
app/alerts.py          thresholds, anti-repetição, pop-up e e-mail (SMTP)
app/storage.py         SQLite (amostras e eventos)
app/config.py          configuração + validação
app/server.py          API Flask
static/                interface (HTML/CSS/JS + Chart.js local)
tests/                 testes unitários
docs/DOCUMENTACAO_TECNICA.md
```

## 8. Evidências para a entrega

Tire capturas (a pasta `docs/evidencias/` está reservada): gráficos em tempo real, histórico com filtro, tela de configurações, pop-up de alerta, e-mail recebido e a faixa de falha SNMP.
