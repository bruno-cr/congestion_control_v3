"""
client.py — Emissor (Cliente) UDP — v3 com Flow Control

Novidade em relação à v2:
  - Controle de fluxo: lê o campo recv_buf de cada ACK recebido
  - Calcula janela_efetiva = min(cwnd, ultimo_recv_buf)
  - Usa janela_efetiva para limitar o envio (em vez de só cwnd)
  - Quando recv_buf = 0, para de enviar e aguarda (sonda via timeout)

Mantido da v2:
  - Slow Start, Congestion Avoidance, Fast Recovery (3 ACKs duplicados)
  - Buffer de envio (buffer_envio) e confirmação via ack_num cumulativo
  - Timeout como fallback de retransmissão
"""

import socket
import time
import random
import json
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from packet import Packet, MSS, RTO, INITIAL_CWND, INITIAL_SSTHRESH

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5001
DATA_SIZE    = 50 * MSS   # 51200 bytes = 50 pacotes


# ─────────────────────────────────────────────────────────────────────────────
# Three-way handshake (igual à v1)
# ─────────────────────────────────────────────────────────────────────────────

def handshake(sock, server_addr):
    isn = random.randint(0, 32767)

    syn = Packet(seq_num=isn, syn=True)
    sock.sendto(syn.to_bytes(), server_addr)
    print(f"[HANDSHAKE] SYN enviado  seq={isn}")

    sock.settimeout(RTO)
    while True:
        try:
            raw, _ = sock.recvfrom(65535)
            resposta = Packet.from_bytes(raw)
            if resposta.syn and resposta.ack:
                print(f"[HANDSHAKE] SYN-ACK recebido  seq={resposta.seq_num}")
                servidor_isn = resposta.seq_num
                break
        except socket.timeout:
            print("[HANDSHAKE] Timeout — reenviando SYN")
            sock.sendto(syn.to_bytes(), server_addr)

    ack = Packet(
        seq_num = (isn + 1) & 0xFFFF,
        ack_num = (servidor_isn + 1) & 0xFFFF,
        ack     = True,
    )
    sock.sendto(ack.to_bytes(), server_addr)
    print(f"[HANDSHAKE] ACK enviado — conexão estabelecida\n")
    return (isn + 1) & 0xFFFF


# ─────────────────────────────────────────────────────────────────────────────
# Loop principal de envio
# ─────────────────────────────────────────────────────────────────────────────

def run_client(host, port, data_size, log_path):

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_addr = (host, port)

    seq_inicial = handshake(sock, server_addr)

    # ── Monta vetor de pacotes ────────────────────────────────────────────
    dados = (bytes(range(256)) * (data_size // 256 + 1))[:data_size]

    pacotes = []
    seq = seq_inicial
    for i in range(0, len(dados), MSS):
        pedaco = dados[i : i + MSS]
        pacotes.append(Packet(seq_num=seq, data=pedaco))
        seq = (seq + len(pedaco)) & 0xFFFF

    # Mapa seq_num → índice no vetor (para converter ACK do servidor em índice)
    seq_para_indice = {p.seq_num: i for i, p in enumerate(pacotes)}

    print(f"[CLIENT] {len(pacotes)} pacotes preparados  ({data_size} bytes)\n")

    # ── Variáveis de controle de congestionamento ─────────────────────────
    cwnd     = float(INITIAL_CWND)
    ssthresh = float(INITIAL_SSTHRESH)
    modo     = "slow_start"

    # ── Variáveis de controle do envio ────────────────────────────────────
    proximo        = 0    # índice do próximo pacote a enviar
    confirmados    = 0    # índice do próximo pacote a confirmar
    retransmissoes = 0
    buffer_envio    = {}   # índice → timestamp (buffer de envio)

    # ── Variáveis do Fast Recovery ────────────────────────────────────────
    acks_duplicados = 0    # contador de ACKs duplicados consecutivos
    ultimo_ack      = -1   # último ack_num recebido (detecta duplicatas)

    # ── Log ───────────────────────────────────────────────────────────────
    log_entries = []
    inicio      = time.time()

    ultimo_recv_buf = INITIAL_SSTHRESH  # valor inicial do recv_buf anunciado pelo servidor (pode ser atualizado a cada ACK)
    janela_efetiva  = cwnd # Inicializando janela efetiva com o valor do CWND, mas ela será atualizada a cada ACK recebido com base no recv_buf anunciado pelo servidor

    def registrar(evento, **extras):
        entrada = {
            "time":     round(time.time() - inicio, 4),
            "event":    evento,
            "cwnd":     round(cwnd),
            "ssthresh": round(ssthresh),
            "modo":     modo,
            "recv_buf": ultimo_recv_buf,
        }
        entrada.update(extras)
        log_entries.append(entrada)

    

    registrar("inicio")
    sock.settimeout(RTO)

    # ── Loop principal ───────────────────────────────────────────────────
    
    
    while confirmados < len(pacotes):
        # Envia pacotes enquanto houver espaço na janela
        pacotes_em_transito = proximo - confirmados
        janela_efetiva = min(cwnd, ultimo_recv_buf)   # ← recalcula a cada iteração
        limite_janela = int(janela_efetiva  / MSS)

        while proximo < len(pacotes) and pacotes_em_transito < limite_janela:
            pkt = pacotes[proximo]
            sock.sendto(pkt.to_bytes(), server_addr)
            buffer_envio[proximo] = time.time()  # guarda no buffer de envio

            print(f"  [ENVIO] pacote={proximo}  seq={pkt.seq_num}"
                  f"  cwnd={round(cwnd)}  modo={modo}")

            proximo += 1
            pacotes_em_transito += 1

        # Aguarda ACK
        try:
            raw, _ = sock.recvfrom(65535)
            ack_pkt = Packet.from_bytes(raw)

            if not ack_pkt.ack:
                continue

            ack_recebido = ack_pkt.ack_num
            # ler o recv_buf desse mesmo pacote ACK para atualizar o espaço disponível no buffer do receptor e calcular a janela efetiva
            ultimo_recv_buf = ack_pkt.recv_buf
            janela_efetiva  = min(cwnd, ultimo_recv_buf)

            if ultimo_recv_buf == 0:
                print(f"  [FLOW-CTRL] recv_buf=0 — receptor sem espaço, envio suspenso (retoma após RTO)")

            # ── ACK duplicado ─────────────────────────────────────────────
            if ack_recebido == ultimo_ack:
                acks_duplicados += 1
                print(f"  [ACK-DUP] ack={ack_recebido}  duplicado #{acks_duplicados}")

                # 3° ACK duplicado — inicia Fast Recovery
                if acks_duplicados == 3:
                    retransmissoes += 1
                    cwnd_anterior = round(cwnd)

                    ssthresh = max(cwnd / 2, MSS)
                    cwnd     = ssthresh + 3 * MSS  # infla a janela
                    modo     = "fast_recovery"

                    print(f"\n  [FAST-RECOVERY] 3 ACKs dup"
                          f"  cwnd: {cwnd_anterior} → {round(cwnd)}"
                          f"  ssthresh={round(ssthresh)}"
                          f"  retransmissao #{retransmissoes}\n")

                    registrar("fast_recovery",
                              cwnd_anterior=cwnd_anterior,
                              numero_retransmissao=retransmissoes)

                    # Retransmite imediatamente o pacote não confirmado
                    pkt = pacotes[confirmados]
                    sock.sendto(pkt.to_bytes(), server_addr)
                    buffer_envio[confirmados] = time.time()
                    proximo = confirmados + 1

                    print(f"  [REENV] pacote={confirmados}  seq={pkt.seq_num}")

                # ACKs duplicados adicionais durante FR inflam +1 MSS
                elif acks_duplicados > 3 and modo == "fast_recovery":
                    cwnd += MSS
                    print(f"  [FR-INFLA] cwnd={round(cwnd)}")

                continue  # ACK dup não confirma nada novo

            # ── ACK novo ─────────────────────────────────────────────────
            ultimo_ack      = ack_recebido
            acks_duplicados = 0

            # Descobre quantos pacotes foram confirmados pelo ack_num
            # O servidor envia ack_num = seq do próximo byte esperado
            # Se ack_num está no mapa, confirma tudo até esse índice
            # Se não está (ACK do último pacote), confirma até o fim
            if ack_recebido in seq_para_indice:
                novo_confirmados = seq_para_indice[ack_recebido]
            else:
                novo_confirmados = len(pacotes)  # último ACK confirma tudo

            # Avança o ponteiro e atualiza CWND para cada pacote confirmado
            while confirmados < novo_confirmados:
                bytes_confirmados = (confirmados + 1) * MSS

                print(f"  [ACK  ] pacote={confirmados}"
                      f"  confirmados={confirmados + 1}/{len(pacotes)}"
                      f"  cwnd={round(cwnd)}  modo={modo}")

                # Saindo do Fast Recovery
                if modo == "fast_recovery":
                    cwnd = ssthresh        # deflaciona para ssthresh
                    modo = "cong_avoid"
                    print(f"  [MODO ] Saindo do Fast Recovery"
                          f" → Congestion Avoidance  cwnd={round(cwnd)}")

                # Slow Start
                elif modo == "slow_start":
                    cwnd += MSS
                    if cwnd >= ssthresh:
                        cwnd = ssthresh
                        modo = "cong_avoid"
                        print(f"  [MODO ] → Congestion Avoidance  cwnd={round(cwnd)}")

                # Congestion Avoidance
                else:
                    cwnd += (MSS * MSS) / cwnd

                registrar("ack",
                          pacote=confirmados,
                          bytes_confirmados=bytes_confirmados)

                confirmados += 1

        except socket.timeout:
            # Timeout — fallback quando FR não foi suficiente
            retransmissoes += 1
            cwnd_anterior   = round(cwnd)

            ssthresh        = max(cwnd / 2, MSS)
            cwnd            = float(MSS)
            modo            = "slow_start"
            acks_duplicados = 0

            print(f"\n  [TIMEOUT] pacote={confirmados}  cwnd: {cwnd_anterior} → 1 MSS"
                  f"  ssthresh={round(ssthresh)}"
                  f"  retransmissao #{retransmissoes}"
                  f"  modo → {modo}\n")

            registrar("timeout",
                      pacote=confirmados,
                      cwnd_anterior=cwnd_anterior,
                      numero_retransmissao=retransmissoes,
                      novo_modo=modo)

            pkt = pacotes[confirmados]
            sock.sendto(pkt.to_bytes(), server_addr)
            buffer_envio[confirmados] = time.time()
            proximo = confirmados + 1

            print(f"  [REENV] pacote={confirmados}  seq={pkt.seq_num}")

    # ── Encerramento ──────────────────────────────────────────────────────
    fin = Packet(seq_num=seq, fin=True)
    sock.sendto(fin.to_bytes(), server_addr)
    print(f"\n[CLIENT] FIN enviado — transmissão concluída")

    # ── Estatísticas ──────────────────────────────────────────────────────
    duracao    = time.time() - inicio
    throughput = data_size / duracao if duracao > 0 else 0

    stats = {
        "total_bytes":            data_size,
        "total_pacotes":          len(pacotes),
        "duracao_segundos":       round(duracao, 3),
        "throughput_bytes_por_s": round(throughput, 2),
        "retransmissoes":         retransmissoes,
    }

    print("\n[CLIENT] ── Estatísticas ──────────────────────────")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    registrar("fim", **stats)

    with open(log_path, "w") as f:
        json.dump({"stats": stats, "events": log_entries}, f, indent=2)
    print(f"[CLIENT] Log salvo em: {log_path}")

    sock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cliente UDP v2 — Fast Recovery")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--size", type=int, default=DATA_SIZE)
    parser.add_argument("--log",  default="client_log.json")
    args = parser.parse_args()

    run_client(args.host, args.port, args.size, args.log)
