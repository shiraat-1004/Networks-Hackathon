#!/usr/bin/env python3
import socket
import struct
import threading
import time
import random
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union
from enum import IntEnum

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='[NET] %(message)s'
)
logger = logging.getLogger(__name__)

# Protocol constants
MAGIC_COOKIE = 0xabcddcba

class MessageType(IntEnum):
    OFFER = 0x2
    REQUEST = 0x3
    PAYLOAD = 0x4


class GameResult(IntEnum):
    NOT_OVER = 0x0
    TIE = 0x1
    LOSS = 0x2
    WIN = 0x3


# Network constants
UDP_LISTEN_PORT = 13122
OFFER_BROADCAST_INTERVAL_SEC = 1.0
CLIENT_TIMEOUT_SEC = 30
OFFER_TIMEOUT_SEC = 10

OFFER_FMT = "!IBH32s"
OFFER_LEN = struct.calcsize(OFFER_FMT)

REQUEST_FMT = "!IBB32s"
REQUEST_LEN = struct.calcsize(REQUEST_FMT)

CLIENT_PAYLOAD_FMT = "!IB5s"
CLIENT_PAYLOAD_LEN = struct.calcsize(CLIENT_PAYLOAD_FMT)

SERVER_PAYLOAD_FMT = "!IBBHB"
SERVER_PAYLOAD_LEN = struct.calcsize(SERVER_PAYLOAD_FMT)

# Decision constants (must be exactly 5 bytes)
DECISION_HIT = "Hittt"
DECISION_STAND = "Stand"


def pad_name(name: str, length: int = 32) -> bytes:
    """Pad a name to fixed length with null bytes."""
    b = name.encode("utf-8", errors="ignore")
    return b[:length].ljust(length, b"\x00")


def unpad_name(b: bytes) -> str:
    """Remove null padding from a name."""
    return b.split(b"\x00", 1)[0].decode("utf-8", errors="ignore")


def recv_exact(conn: socket.socket, n: int) -> bytes:
    """Receive exactly n bytes or raise ConnectionError."""
    data = b""
    while len(data) < n:
        chunk = conn.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Connection closed by peer")
        data += chunk
    return data


@dataclass(frozen=True)
class Card:
    """Represents a playing card."""
    rank: int  # 1..13 (Ace=1, Jack=11, Queen=12, King=13)
    suit: int  # 0..3 (Hearts, Diamonds, Clubs, Spades)


CardOrRank = Union[Card, int]


def _get_rank(x: CardOrRank) -> int:
    return x.rank if isinstance(x, Card) else int(x)


def card_value(card_or_rank: CardOrRank) -> int:
    """Calculate Blackjack value (Ace=11, Face=10). Accepts Card or rank int."""
    rank = _get_rank(card_or_rank)
    if rank == 1:
        return 11
    if 2 <= rank <= 10:
        return rank
    if 11 <= rank <= 13:
        return 10
    # rank=0 means "no card" in your protocol
    return 0


def card_str(card_or_rank: Union[Card, int], suit: Optional[int] = None) -> str:
    """
    Convert card to human-readable string.
    Accepts either:
      - Card instance
      - (rank:int, suit:int)
    """
    if isinstance(card_or_rank, Card):
        rank = card_or_rank.rank
        s = card_or_rank.suit
    else:
        rank = int(card_or_rank)
        s = 0 if suit is None else int(suit)

    ranks = {1: "A", 11: "J", 12: "Q", 13: "K"}
    suits = {0: "♥", 1: "♦", 2: "♣", 3: "♠"}

    rank_str = ranks.get(rank, str(rank))
    suit_str = suits.get(s, "?")
    return f"{rank_str}{suit_str}"


def fresh_shuffled_deck() -> List[Card]:
    """Create and shuffle a new 52-card deck."""
    deck = [Card(rank=r, suit=s) for s in range(4) for r in range(1, 14)]
    random.shuffle(deck)
    return deck
