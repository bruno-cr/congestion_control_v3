"""
plot_results.py — Gera gráfico CWND × tempo (v3 com Flow Control)

Mostra:
  - CWND ao longo do tempo (azul)
  - SSTHRESH ao longo do tempo (laranja tracejado)
  - Recv Buffer anunciado pelo servidor ao longo do tempo (teal)
  - Linhas vermelhas nos timeouts
  - Linhas verdes nos eventos de Fast Recovery
  - Anotações de transição de modo
"""

import json
import os
import argparse
import sys

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    print("Instale matplotlib:  pip install matplotlib")
    sys.exit(1)


def carregar(path):
    with open(path) as f:
        return json.load(f)


def gerar_grafico(client_log, pasta_saida):
    os.makedirs(pasta_saida, exist_ok=True)

    cliente = carregar(client_log)
    eventos = cliente["events"]
    stats   = cliente["stats"]

    # ── Extrai séries do log ──────────────────────────────────────────────

    tempos_cwnd, cwnd_vals, ssthresh_vals = [], [], []
    tempos_timeout      = []
    tempos_fast_recovery = []
    tempos_recv_buf, recv_buf_vals = [], []

    for e in eventos:
        t = e["time"]

        if "cwnd" in e:
            tempos_cwnd.append(t)
            cwnd_vals.append(e["cwnd"] / 1024)        # bytes → kB
            ssthresh_vals.append(e["ssthresh"] / 1024)
        
        if "recv_buf" in e:
            tempos_recv_buf.append(t)
            recv_buf_vals.append(e["recv_buf"] / 1024)        # bytes → kB

        if e["event"] == "timeout":
            tempos_timeout.append(t)

        if e["event"] == "fast_recovery":
            tempos_fast_recovery.append(t)

    # ── Gráfico ───────────────────────────────────────────────────────────

    fig, ax = plt.subplots(figsize=(12, 6))
    fig.suptitle("Controle de Congestionamento TCP sobre UDP — v3 (Flow Control)",
                 fontsize=13, fontweight="bold")

    # CWND e SSTHRESH
    ax.plot(tempos_cwnd, cwnd_vals,     label="CWND (kB)",     color="royalblue",  linewidth=2)
    ax.plot(tempos_cwnd, ssthresh_vals, label="SSTHRESH (kB)", color="darkorange", linewidth=1.5, linestyle="--")

    # recv_buf anunciado pelo servidor
    ax.plot(tempos_recv_buf, recv_buf_vals, label="Recv Buffer (kB)", color="teal", linewidth=1.5, linestyle="-.")

    # Linhas verticais de timeout
    for t in tempos_timeout:
        ax.axvline(x=t, color="red", alpha=0.7, linewidth=1.2, linestyle=":")
    if tempos_timeout:
        ax.axvline(x=tempos_timeout[0], color="red", alpha=0.7,
                   linewidth=1.2, linestyle=":", label="Timeout")

    # Linhas verticais de Fast Recovery
    for t in tempos_fast_recovery:
        ax.axvline(x=t, color="green", alpha=0.7, linewidth=1.2, linestyle="--")
    if tempos_fast_recovery:
        ax.axvline(x=tempos_fast_recovery[0], color="green", alpha=0.7,
                   linewidth=1.2, linestyle="--", label="Fast Recovery (3 ACKs dup)")

    # Anotações de transição de modo
    em_ca = False
    em_fr = False
    for e in eventos:
        if e["event"] == "ack" and e.get("modo") == "cong_avoid" and not em_ca and not em_fr:
            ax.annotate("→ Cong. Avoidance",
                        xy=(e["time"], e["cwnd"] / 1024),
                        xytext=(e["time"] + min(0.05, (tempos_cwnd[-1] - e["time"]) * 0.3), e["cwnd"] / 1024 + 1),
                        fontsize=8, color="darkblue",
                        arrowprops=dict(arrowstyle="->", color="darkblue"))
            em_ca = True
        if e["event"] == "fast_recovery":
            em_ca = False
            em_fr = True
        if e["event"] == "timeout":
            em_ca = False
            em_fr = False

    ax.set_title("Janela de Congestionamento (CWND) ao Longo do Tempo")
    ax.set_xlabel("Tempo (s)")
    ax.set_ylabel("Tamanho (kB)")
    ax.legend()
    ax.grid(True, linestyle="--", alpha=0.5)

    # Estatísticas no gráfico
    resumo = (f"Pacotes: {stats['total_pacotes']}  |  "
              f"Retransmissões: {stats['retransmissoes']}  |  "
              f"Throughput: {stats['throughput_bytes_por_s']/1024:.1f} kB/s  |  "
              f"Duração: {stats['duracao_segundos']}s")
    ax.annotate(resumo, xy=(0.01, 0.02), xycoords="axes fraction",
                fontsize=8, color="gray",
                bbox=dict(boxstyle="round,pad=0.3", fc="white", alpha=0.7))

    # ── Salva ─────────────────────────────────────────────────────────────

    plt.tight_layout()
    saida = os.path.join(pasta_saida, "cwnd_vs_tempo.png")
    plt.savefig(saida, dpi=150, bbox_inches="tight")
    print(f"Gráfico salvo em: {saida}")

    # ── Estatísticas no terminal ──────────────────────────────────────────

    print("\n── Estatísticas ──────────────────────────────────────")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gera gráfico CWND × tempo (v2)")
    parser.add_argument("--client-log", default="client_log.json")
    parser.add_argument("--out-dir",    default="graphs")
    args = parser.parse_args()

    gerar_grafico(args.client_log, args.out_dir)
