# Controle de Congestionamento Minimalista usando UDP — v3

> Atividade — Tópicos Avançados em Arquiteturas Distribuídas de Software  
> PPGCC — UFSCar · Prof. Dr. Fábio Luciano Verdi

> Autor: Bruno Camargo Ribeiro

---

## 1. Diferenças em relação às versões anteriores

|                                   | v1 | v2 | v3 |
|                                   |    |    |    |
| Slow Start                        | ✅ | ✅ | ✅|
| Congestion Avoidance              | ✅ | ✅ | ✅|
| Timeout                           | ✅ | ✅ | ✅|
| Fast Recovery                     | ❌ | ✅ | ✅|
| Buffer no cliente                 | ❌ | ✅ | ✅|
| Buffer de reordenação no servidor | ❌ | ✅ | ✅|
| Controle de Fluxo (Flow Control)  | ❌ | ❌ | ✅|
| Campo recv_buf no header          | ❌ | ❌ | ✅|
| Gráfico CWND × tempo              | ✅ | ✅ | ✅|
| Curva recv_buf no gráfico         | ❌ | ❌ | ✅|

---

## 2. Visão Geral da Arquitetura

```
┌──────────────────────────────────────────────────────────┐
│                      packet.py                           │
│   Header TCP simplificado (8 bytes) sobre UDP            │
│   seq(16) | ack(16) | recv_buf(16) | datalen(13)+flags(3)│
└────────────────────────┬─────────────────────────────────┘
                         │ importado por
           ┌─────────────┴──────────────┐
           ▼                            ▼
    client.py (Emissor)         server.py (Receptor)
    ─────────────────────       ──────────────────────
    • Three-way handshake       • Three-way handshake
    • Vetor de pacotes          • ACKs cumulativos
    • Slow Start                • Simulação de perda
    • Congestion Avoidance      • Buffer de reordenação
    • Timeout                   • Anuncia recv_buf em cada ACK
    • Fast Recovery             • BUFFER_RECEPTOR = 16 MSS
    • buffer_envio              • Log JSON
    • Lê recv_buf dos ACKs
    • janela_efetiva = min(cwnd, recv_buf)
    • Log JSON
           │
           ▼
    plot_results.py
    ───────────────
    • Gráfico CWND × tempo
    • Curva recv_buf anunciado
    • Marcação de timeouts
    • Marcação de Fast Recovery
```

---

## 3. Estrutura do Pacote

Header de **8 bytes** definido em `packet.py`. Na v3 o campo `recv_buf` passa a ser
utilizado de fato — o servidor o preenche em cada ACK e o cliente o lê para calcular
a janela efetiva:

```
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|        Número de Sequência    |        Número de ACK          |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|   Buffer de Recebimento ←NEW  |   Data Length (13 bits) |A|S|F|
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

---

## 4. Parâmetros do Protocolo

| Parâmetro        | Valor        | Descrição                              |
|------------------|--------------|----------------------------------------|
| MSS              | 1024 bytes   | Tamanho máximo de segmento             |
| PACKET_SIZE      | 1032 bytes   | MSS + Header (8 bytes)                 |
| RTO              | 500 ms       | Retransmission Timeout                 |
| CWND (inicial)   | 1024 bytes   | Janela de congestionamento inicial     |
| SSTHRESH         | 15360 bytes  | Limiar inicial = 15 × MSS              |
| BUFFER_RECEPTOR  | 16384 bytes  | Tamanho do buffer do receptor = 16 MSS |

---

## 5. Algoritmos de Controle de Congestionamento

### 5.1 Slow Start

```
CWND = CWND + 1 MSS   (por ACK recebido)
```

Crescimento exponencial até atingir SSTHRESH.

### 5.2 Congestion Avoidance

```
CWND = CWND + (MSS × MSS) / CWND   (por ACK recebido)
```

Crescimento linear de ~1 MSS por RTT.

### 5.3 Timeout

```
SSTHRESH = max(CWND / 2, MSS)
CWND     = 1 MSS
modo     = slow_start
```

Congestionamento severo — recomeça do zero.

### 5.4 Fast Recovery

Ativado ao receber **3 ACKs duplicados consecutivos**:

```
SSTHRESH = max(CWND / 2, MSS)
CWND     = SSTHRESH + 3 MSS
modo     = fast_recovery
```

Quando o ACK novo chega:
```
CWND = SSTHRESH
modo = cong_avoid   ← entra direto em CA, sem Slow Start
```

---

## 6. Controle de Fluxo — novidade da v3

O controle de fluxo impede que o emissor sobrecarregue o buffer do receptor.
O receptor anuncia continuamente quanto espaço tem disponível via campo `recv_buf`
do header, e o emissor respeita esse limite combinando as duas janelas.

### 6.1 No servidor

O servidor define um buffer fixo de `BUFFER_RECEPTOR = 16 MSS` (16384 bytes).
A cada ACK enviado, calcula o espaço disponível e o anuncia no campo `recv_buf`:

```python
bytes_buffer      = sum(len(pkt.data) for pkt in buffer.values())
espaco_disponivel = max(0, BUFFER_RECEPTOR - bytes_buffer)
Packet(ack_num=seq_esperado, ack=True, recv_buf=espaco_disponivel)
```

O `max(0, ...)` garante que o campo nunca fique negativo — o campo é unsigned (16 bits).

### 6.2 No cliente

O cliente lê o `recv_buf` de cada ACK recebido e calcula a **janela efetiva**:

```python
ultimo_recv_buf = ack_pkt.recv_buf
janela_efetiva  = min(cwnd, ultimo_recv_buf)
limite_janela   = int(janela_efetiva / MSS)
```

O cliente nunca envia mais pacotes do que `janela_efetiva` permite — respeitando
tanto o congestionamento da rede (CWND) quanto a capacidade do receptor (recv_buf).

### 6.3 Quando recv_buf = 0

Se o receptor anunciar `recv_buf = 0`, o cliente suspende o envio e aguarda o RTO.
Após o timeout, retransmite o último pacote não confirmado, que funciona como
uma sonda — o servidor responde com ACK anunciando o novo espaço disponível.

```
recv_buf = 0 → cliente para de enviar
            → aguarda RTO (500ms)
            → retransmite pacote (sonda)
            → servidor responde com recv_buf > 0
            → cliente retoma o envio
```

---

## 7. Buffer de Reordenação (servidor)

Os pacotes fora de ordem são guardados em `buffer = { seq_num → Packet }`.
Quando o pacote esperado chega, o servidor entrega em cascata tudo que estava
no buffer, gerando ACKs cumulativos — mecanismo que permite ao cliente acumular
3 ACKs duplicados e acionar o Fast Recovery.

O tamanho do buffer impacta diretamente o `recv_buf` anunciado:
quanto mais pacotes fora de ordem estiverem no buffer, menor o espaço disponível
anunciado ao cliente.

---

## 8. Buffer de Envio (cliente)

O cliente mantém `buffer_envio = { índice → timestamp }` com todos os pacotes
em trânsito. Permite retransmitir imediatamente no Fast Recovery sem esperar o
timeout, e serve como referência para calcular o RTT.

---

## 9. Three-Way Handshake

Igual às versões anteriores:

```
Cliente                                Servidor
   │                                      │
   │──── SYN (seq=ISN_c) ───────────────►│
   │                                      │  gera ISN_s
   │◄─── SYN-ACK (seq=ISN_s, ack=ISN_c+1)│
   │                                      │
   │──── ACK (ack=ISN_s+1) ─────────────►│
   │                                      │
   │═══════════════ DADOS ════════════════│
```

---

## 10. Estrutura de Arquivos

```
congestion_control_v3/
├── packet.py        # Estrutura do pacote — recv_buf agora utilizado
├── client.py        # Emissor: Flow Control + Fast Recovery + buffer_envio
├── server.py        # Receptor: anuncia recv_buf + buffer de reordenação
├── plot_results.py  # Gráfico CWND × tempo + curva recv_buf
├── README.md        # Este arquivo
├── client_log.json  # (gerado ao executar o cliente)
└── server_log.json  # (gerado ao executar o servidor)
```

---

## 11. Instalação

```bash
pip install matplotlib
```

---

## 12. Executando em Localhost

**Terminal 1 — Servidor:**
```bash
python3 server.py --drop 0.05 --log server_log.json
```

**Terminal 2 — Cliente:**
```bash
python3 client.py --size 51200 --log client_log.json
```

**Gerar gráfico:**
```bash
python3 plot_results.py
# Saída: graphs/cwnd_vs_tempo.png
```

---

## 13. Executando com VM

Copiar arquivos para a VM:

```bash
ssh aluno@192.168.56.102 'mkdir -p ~/congestion_control_v3'
scp packet.py server.py aluno@192.168.56.102:~/congestion_control_v3/
```

Na VM:
```bash
python3 server.py --host 0.0.0.0 --drop 0.05 --log server_log.json
```

No host:
```bash
python3 client.py --host 192.168.56.102 --size 51200 --log client_log.json
```

Copiar log e gerar gráfico:
```bash
scp aluno@192.168.56.102:~/congestion_control_v3/server_log.json .
python3 plot_results.py
```

---

## 14. Simulação de rede com tc + netem

Aplicar na VM (interface Host-Only):

```bash
# WAN típica
sudo tc qdisc add dev enp0s8 root netem delay 50ms 10ms distribution normal loss 5%

# Perda alta
sudo tc qdisc add dev enp0s8 root netem delay 100ms 25ms distribution normal loss 15%

# Remover
sudo tc qdisc del dev enp0s8 root
```

> Ao usar netem com perda, use `--drop 0.0` no servidor para não acumular perdas.

---