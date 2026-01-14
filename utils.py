#!/usr/bin/env python3


from __future__ import annotations

import os
import socket
import struct
import sys
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, Tuple


# =========================
# ANSI colors (no external deps)
# =========================
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    GRAY = "\033[90m"


def enable_ansi_on_windows() -> None:
    """
    Try to enable ANSI escape sequences on Windows terminals.
    If it fails, we just live with raw escapes (still mostly OK on modern Win10/11).
    """
    if os.name != "nt":
        return
    try:
        os.system("")  # triggers VT processing in many terminals
    except Exception:
        pass


def color(text: str, col: str) -> str:
    return f"{col}{text}{C.RESET}"


def banner(title: str) -> str:
    line = "=" * 60
    return f"\n{color(line, C.CYAN)}\n{color(title.center(60), C.BOLD + C.CYAN)}\n{color(line, C.CYAN)}"


# =========================
# Protocol constants
# =========================
MAGIC_COOKIE = 0xabcddcba

UDP_LISTEN_PORT = 13122  # client must listen here
OFFER_BROADCAST_INTERVAL_SEC = 1.0

CLIENT_TIMEOUT_SEC = 30
OFFER_TIMEOUT_SEC = 10

TEAM_NAME_LEN = 32
DECISION_LEN = 5


class MessageType(IntEnum):
    OFFER = 0x2
    REQUEST = 0x3
    PAYLOAD = 0x4


class GameResult(IntEnum):
    NOT_OVER = 0x0
    TIE = 0x1
    LOSS = 0x2
    WIN = 0x3


# Offer: cookie(4) type(1) tcp_port(2) name(32)
OFFER_FMT = "!IBH32s"
OFFER_LEN = struct.calcsize(OFFER_FMT)

# Request: cookie(4) type(1) rounds(1) name(32)
REQUEST_FMT = "!IBB32s"
REQUEST_LEN = struct.calcsize(REQUEST_FMT)

# Client payload: cookie(4) type(1) decision(5 bytes)
CLIENT_PAYLOAD_FMT = "!IB5s"
CLIENT_PAYLOAD_LEN = struct.calcsize(CLIENT_PAYLOAD_FMT)

# Server payload: cookie(4) type(1) result(1) rank(2) suit(1)
# rank encoded 01-13 in first 2 bytes, suit 0-3 in second byte (HDCS)
SERVER_PAYLOAD_FMT = "!IBBHB"
SERVER_PAYLOAD_LEN = struct.calcsize(SERVER_PAYLOAD_FMT)

DECISION_HIT = "Hittt"
DECISION_STAND = "Stand"


# =========================
# Helpers: bytes + recv
# =========================
def pad_name(name: str, length: int = TEAM_NAME_LEN) -> bytes:
    b = name.encode("utf-8", errors="ignore")
    return b[:length].ljust(length, b"\x00")


def unpad_name(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("utf-8", errors="ignore")


def recv_exact(conn: socket.socket, n: int) -> bytes:
    """
    Receive exactly n bytes or raise ConnectionError.
    No busy-waiting: blocking recv with socket timeout controlled by caller.
    """
    data = b""
    while len(data) < n:
        chunk = conn.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Connection closed by peer")
        data += chunk
    return data


def maybe_consume_newline(conn: socket.socket) -> None:
    """
    The assignment's example mentions a '\\n' after the TCP request.
    Some teams might send it, some won't.
    We'll try to consume a single newline if it exists WITHOUT blocking.
    """
    try:
        conn.settimeout(0.01)
        b = conn.recv(1)
        if b not in (b"\n", b"\r"):
            # If it's not newline, put it back is impossible with plain sockets,
            # so we avoid consuming real payload by only calling this right after request.
            # If we got something else, we just keep it as a protocol mismatch scenario.
            # But in practice: nobody should send extra bytes right after request except newline.
            pass
    except Exception:
        pass
    finally:
        try:
            conn.settimeout(CLIENT_TIMEOUT_SEC)
        except Exception:
            pass


# =========================
# Card utils
# =========================
@dataclass(frozen=True)
class Card:
    rank: int  # 1..13
    suit: int  # 0..3 (H D C S)


def card_value_rank(rank: int) -> int:
    if rank == 1:
        return 11
    if 2 <= rank <= 10:
        return rank
    if 11 <= rank <= 13:
        return 10
    return 0


def card_value(card: Card) -> int:
    return card_value_rank(card.rank)


def card_str(rank: int, suit: int) -> str:
    ranks = {1: "A", 11: "J", 12: "Q", 13: "K"}
    suits = {0: "♥", 1: "♦", 2: "♣", 3: "♠"}  # H D C S
    r = ranks.get(rank, str(rank))
    s = suits.get(suit, "?")
    return f"{r}{s}"


# =========================
# Network convenience
# =========================
def get_local_ip() -> str:
    """
    Reliable local IP (best effort). Used for printing only.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "0.0.0.0"


def guess_broadcast_address(local_ip: str) -> str:
    """
    Best-effort broadcast address.
    Many networks are /24 in hackathons; if not, <broadcast> is still useful.
    """
    try:
        parts = local_ip.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.{parts[2]}.255"
    except Exception:
        pass
    return "<broadcast>"
