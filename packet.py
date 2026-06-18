"""
packet.py — Estrutura do pacote TCP simplificado sobre UDP

Header de 8 bytes (4 campos de 16 bits):
  [seq_num] [ack_num] [recv_buf] [data_len(13bits) + flags ACK/SYN/FIN(3bits)]
"""

import struct

# ── Constantes do protocolo ───────────────────────────────────────────────────
HEADER_FORMAT    = "!HHHH"                      # 4 × unsigned short = 8 bytes
HEADER_SIZE      = struct.calcsize(HEADER_FORMAT)  # 8 bytes
MSS              = 1024    # Maximum Segment Size (bytes de dados por pacote)
PACKET_SIZE      = HEADER_SIZE + MSS             # 1032 bytes (tamanho máximo)
RTO              = 0.5     # Retransmission Timeout em segundos
INITIAL_CWND     = MSS     # Janela inicial = 1 MSS
INITIAL_SSTHRESH = 15360   # Limiar inicial = 15 MSS

# ── Flags (ocupam os 3 bits menos significativos do último campo) ─────────────
FLAG_ACK = 0b001  # bit 0
FLAG_SYN = 0b010  # bit 1
FLAG_FIN = 0b100  # bit 2


class Packet:
    """Representa um pacote TCP simplificado."""

    def __init__(self, seq_num=0, ack_num=0, recv_buf=0,
                 data=b"", ack=False, syn=False, fin=False):
        self.seq_num  = seq_num   # número de sequência
        self.ack_num  = ack_num   # número de confirmação
        self.recv_buf = recv_buf  # buffer de recebimento
        self.data     = data      # payload de dados
        self.ack      = ack       # flag ACK
        self.syn      = syn       # flag SYN
        self.fin      = fin       # flag FIN

    def to_bytes(self):
        """Serializa o pacote em bytes para envio pelo socket UDP."""
        # Empacota data_len nos 13 bits superiores e flags nos 3 bits inferiores
        flags = (self.ack * FLAG_ACK) | (self.syn * FLAG_SYN) | (self.fin * FLAG_FIN)
        ultimo_campo = ((len(self.data) & 0x1FFF) << 3) | flags

        header = struct.pack(
            HEADER_FORMAT,
            self.seq_num  & 0xFFFF,
            self.ack_num  & 0xFFFF,
            self.recv_buf & 0xFFFF,
            ultimo_campo  & 0xFFFF,
        )
        return header + self.data

    @classmethod
    def from_bytes(cls, raw):
        """Desserializa bytes recebidos do socket em um objeto Packet."""
        seq, ack_n, rbuf, ultimo = struct.unpack(HEADER_FORMAT, raw[:HEADER_SIZE])

        # Extrai data_len e flags do último campo
        data_len = (ultimo >> 3) & 0x1FFF
        flags    = ultimo & 0b111

        return cls(
            seq_num  = seq,
            ack_num  = ack_n,
            recv_buf = rbuf,
            data     = raw[HEADER_SIZE : HEADER_SIZE + data_len],
            ack      = bool(flags & FLAG_ACK),
            syn      = bool(flags & FLAG_SYN),
            fin      = bool(flags & FLAG_FIN),
        )

    def __repr__(self):
        flags = f"{'A' if self.ack else '.'}{'S' if self.syn else '.'}{'F' if self.fin else '.'}"
        return f"Packet(seq={self.seq_num}, ack={self.ack_num}, len={len(self.data)}, flags=[{flags}])"
