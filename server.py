"""
server.py — Receptor (Servidor) UDP — v3 com Flow Control

Novidade em relação à v2:
  - Controle de fluxo: anuncia o espaço disponível no buffer em cada ACK (campo recv_buf)
  - BUFFER_RECEPTOR define o tamanho fixo do buffer de recebimento (16 MSS)
  - Espaço disponível = BUFFER_RECEPTOR - bytes atualmente no buffer de reordenação

Mantido da v2:
  - Buffer de reordenação: pacotes fora de ordem são guardados em vez de descartados
  - Quando o pacote esperado chega, entrega tudo que estava bufferizado em sequência
  - ACK cumulativo do último seq entregue em ordem
"""

import socket
import random
import time
import json
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from packet import Packet, MSS, RTO

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5001
DROP_PADRAO  = 0.10
BUFFER_RECEPTOR = 16 * MSS  # Buffer do receptor (16 MSS = 16 KB)


def run_server(host, port, drop_prob, log_path):

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, port))
    print(f"[SERVER] Escutando em {host}:{port}  (drop={drop_prob:.0%})")

    # ── 1. THREE-WAY HANDSHAKE ────────────────────────────────────────────

    while True:
        raw, cliente = sock.recvfrom(65535)
        pkt = Packet.from_bytes(raw)
        if pkt.syn and not pkt.ack:
            print(f"[HANDSHAKE] SYN recebido  seq={pkt.seq_num}")
            break

    servidor_isn = random.randint(1000, 60000)
    seq_esperado = (pkt.seq_num + 1) & 0xFFFF

    syn_ack = Packet(seq_num=servidor_isn, ack_num=seq_esperado, syn=True, ack=True)
    sock.sendto(syn_ack.to_bytes(), cliente)
    print(f"[HANDSHAKE] SYN-ACK enviado  seq={servidor_isn}  ack={seq_esperado}")

    raw, _ = sock.recvfrom(65535)
    print(f"[HANDSHAKE] ACK recebido — conexão estabelecida\n")

    # ── 2. RECEBIMENTO DE DADOS ───────────────────────────────────────────

    sock.settimeout(5.0)
    inicio         = time.time()
    total_bytes    = 0
    retransmissoes = 0
    log_entries    = []

    # Buffer de reordenação: guarda pacotes fora de ordem
    # chave = seq_num do pacote, valor = objeto Packet
    buffer = {}

    def registrar(evento, **extras):
        entrada = {"time": round(time.time() - inicio, 4), "event": evento}
        entrada.update(extras)
        log_entries.append(entrada)

    print("[SERVER] Aguardando dados...\n")

    while True:
        try:
            raw, addr = sock.recvfrom(65535)
        except socket.timeout:
            print("[SERVER] Timeout — encerrando.")
            break

        pkt = Packet.from_bytes(raw)
        agora = round(time.time() - inicio, 3)

        # FIN: encerra conexão
        if pkt.fin:
            print("[SERVER] FIN recebido — encerrando conexão.")
            sock.sendto(Packet(fin=True, ack=True).to_bytes(), addr)
            break

        # Simula descarte do pacote
        if random.random() < drop_prob:
            print(f"  [DROP-PKT] seq={pkt.seq_num}  t={agora}s")
            retransmissoes += 1
            registrar("drop_pacote", seq=pkt.seq_num)
            continue

        # Pacote em ordem — entrega imediata
        if pkt.seq_num == seq_esperado:
            total_bytes  += len(pkt.data)
            seq_esperado  = (pkt.seq_num + len(pkt.data)) & 0xFFFF
            print(f"  [OK] seq={pkt.seq_num}  bytes={len(pkt.data)}"
                  f"  total={total_bytes}  t={agora}s")
            registrar("recebido", seq=pkt.seq_num, bytes=len(pkt.data), total=total_bytes)

            # Verifica se há pacotes bufferizados que agora estão em ordem
            while seq_esperado in buffer:
                pkt_buf = buffer.pop(seq_esperado)
                total_bytes  += len(pkt_buf.data)
                seq_esperado  = (pkt_buf.seq_num + len(pkt_buf.data)) & 0xFFFF
                print(f"  [BUF-ENTREGA] seq={pkt_buf.seq_num}  total={total_bytes}  t={agora}s")
                registrar("entrega_buffer", seq=pkt_buf.seq_num, total=total_bytes)

        # Pacote fora de ordem — armazena no buffer
        else:
            if pkt.seq_num not in buffer:
                buffer[pkt.seq_num] = pkt
                print(f"  [BUFFER] seq={pkt.seq_num}  esperado={seq_esperado}"
                      f"  buffer_size={len(buffer)}  t={agora}s")
                registrar("bufferizado", seq=pkt.seq_num, esperado=seq_esperado,
                          buffer_size=len(buffer))
            else:
                print(f"  [DUP-BUFFER] seq={pkt.seq_num} já no buffer  t={agora}s")

        # Simula descarte do ACK
        if random.random() < drop_prob:
            print(f"  [DROP-ACK] ack={seq_esperado}  t={agora}s")
            registrar("drop_ack", ack=seq_esperado)
            continue

        # Cálculo de bytes no buffer e espaço disponível
        bytes_buffer = sum(len(pkt.data) for pkt in buffer.values())
        espaco_disponivel = max(0, BUFFER_RECEPTOR - bytes_buffer)
        # Envia ACK cumulativo do último seq entregue em ordem
        sock.sendto(Packet(ack_num=seq_esperado, ack=True, recv_buf=espaco_disponivel).to_bytes(), addr)

    # ── 3. ESTATÍSTICAS E LOG ─────────────────────────────────────────────

    duracao    = time.time() - inicio
    throughput = total_bytes / duracao if duracao > 0 else 0

    stats = {
        "total_bytes":            total_bytes,
        "duracao_segundos":       round(duracao, 3),
        "throughput_bytes_por_s": round(throughput, 2),
        "retransmissoes":         retransmissoes,
    }

    print("\n[SERVER] ── Estatísticas ─────────────────────────────")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    with open(log_path, "w") as f:
        json.dump({"stats": stats, "events": log_entries}, f, indent=2)
    print(f"[SERVER] Log salvo em: {log_path}")
    sock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Servidor UDP v2 — Fast Recovery")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--drop", type=float, default=DROP_PADRAO)
    parser.add_argument("--log",  default="server_log.json")
    args = parser.parse_args()

    run_server(args.host, args.port, args.drop, args.log)
