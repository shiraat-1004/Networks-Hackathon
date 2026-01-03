#!/usr/bin/env python3
"""
Blackjack Server - Hosts a Blackjack game for connected clients.
Supports multiple concurrent clients via threading.
"""
import socket
import struct
import threading
import time
import random
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple
from enum import IntEnum

# =========================
# Logging setup
# =========================
logging.basicConfig(
    level=logging.INFO,
    format='[SERVER] %(message)s'
)
logger = logging.getLogger(__name__)

# =========================
# Protocol constants
# =========================
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
UDP_LISTEN_PORT_CLIENTS = 13122
OFFER_BROADCAST_INTERVAL_SEC = 1.0
CLIENT_TIMEOUT_SEC = 30

# Struct formats
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

# =========================
# Helper functions
# =========================
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

# =========================
# Card handling
# =========================
@dataclass(frozen=True)
class Card:
    """Represents a playing card."""
    rank: int  # 1..13 (Ace=1, Jack=11, Queen=12, King=13)
    suit: int  # 0..3 (Hearts, Diamonds, Clubs, Spades)

def card_value(card: Card) -> int:
    """Calculate the Blackjack value of a card (Ace=11, Face cards=10)."""
    if card.rank == 1:
        return 11
    if 2 <= card.rank <= 10:
        return card.rank
    return 10  # Face cards

def card_str(card: Card) -> str:
    """Convert card to human-readable string."""
    ranks = {1: "A", 11: "J", 12: "Q", 13: "K"}
    suits = {0: "♥", 1: "♦", 2: "♣", 3: "♠"}
    rank_str = ranks.get(card.rank, str(card.rank))
    suit_str = suits.get(card.suit, "?")
    return f"{rank_str}{suit_str}"

def fresh_shuffled_deck() -> List[Card]:
    """Create and shuffle a new 52-card deck."""
    deck = [Card(rank=r, suit=s) for s in range(4) for r in range(1, 14)]
    random.shuffle(deck)
    return deck

# =========================
# Server class
# =========================
class BlackjackServer:
    """Blackjack game server that handles multiple clients."""
    
    def __init__(self, team_name: str):
        self.team_name = team_name
        self.team_name_bytes = pad_name(team_name, 32)
        self.running = True
        
        # TCP server socket (pick any free port)
        self.tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp_sock.bind(("", 0))
        self.tcp_sock.listen()
        self.tcp_port = self.tcp_sock.getsockname()[1]
        
        # UDP broadcast socket
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        
        # Try to get broadcast address for the network
        self.broadcast_addr = self._get_broadcast_address()
    
    def start(self) -> None:
        """Start the server."""
        ip = self._get_local_ip()
        
        print("\n" + "=" * 50)
        print("       🃏 BLACKJACK SERVER 🃏")
        print("=" * 50)
        logger.info(f"Server started on IP {ip}, TCP port {self.tcp_port}")
        logger.info(f"Broadcasting offers on UDP port {UDP_LISTEN_PORT_CLIENTS}")
        logger.info(f"Broadcast address: {self.broadcast_addr}")
        print("=" * 50 + "\n")
        
        # Start offer broadcast thread
        offer_thread = threading.Thread(target=self._offer_loop, daemon=True)
        offer_thread.start()
        
        try:
            while True:
                conn, addr = self.tcp_sock.accept()
                conn.settimeout(CLIENT_TIMEOUT_SEC)
                logger.info(f"New connection from {addr[0]}:{addr[1]}")
                
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(conn, addr),
                    daemon=True
                )
                client_thread.start()
                
        except KeyboardInterrupt:
            print("\n")
            logger.info("Shutting down server...")
        finally:
            self._cleanup()
    
    def _cleanup(self) -> None:
        """Clean up server resources."""
        self.running = False
        try:
            self.tcp_sock.close()
        except Exception:
            pass
        try:
            self.udp_sock.close()
        except Exception:
            pass
    
    def _offer_loop(self) -> None:
        """Continuously broadcast offers to potential clients."""
        offer = struct.pack(
            OFFER_FMT,
            MAGIC_COOKIE,
            MessageType.OFFER,
            self.tcp_port,
            self.team_name_bytes
        )
        
        while self.running:
            try:
                self.udp_sock.sendto(offer, (self.broadcast_addr, UDP_LISTEN_PORT_CLIENTS))
            except Exception as e:
                logger.warning(f"Offer broadcast error: {e}")
            time.sleep(OFFER_BROADCAST_INTERVAL_SEC)
    
    def _handle_client(self, conn: socket.socket, addr: Tuple[str, int]) -> None:
        """Handle a connected client through their game session."""
        try:
            # Read game request
            req = recv_exact(conn, REQUEST_LEN)
            cookie, mtype, rounds, cname_bytes = struct.unpack(REQUEST_FMT, req)
            
            if cookie != MAGIC_COOKIE or mtype != MessageType.REQUEST:
                logger.warning(f"Invalid request from {addr}: protocol mismatch")
                conn.close()
                return
            
            client_name = unpad_name(cname_bytes)
            num_rounds = int(rounds)
            
            logger.info(f"Client '{client_name}' wants to play {num_rounds} rounds")
            
            # Play all rounds
            wins = losses = ties = 0
            
            for r in range(1, num_rounds + 1):
                result = self._play_one_round(conn, client_name, r)
                if result == GameResult.WIN:
                    wins += 1
                elif result == GameResult.LOSS:
                    losses += 1
                else:
                    ties += 1
            
            logger.info(f"Session with '{client_name}' complete: W={wins} L={losses} T={ties}")
            conn.close()
            
        except ConnectionError:
            logger.info(f"Client {addr} disconnected")
            self._safe_close(conn)
        except socket.timeout:
            logger.info(f"Client {addr} timed out")
            self._safe_close(conn)
        except Exception as e:
            logger.error(f"Error with client {addr}: {e}")
            self._safe_close(conn)
    
    def _safe_close(self, conn: socket.socket) -> None:
        """Safely close a connection."""
        try:
            conn.close()
        except Exception:
            pass
    
    def _send_card(self, conn: socket.socket, result_code: int, card: Optional[Card]) -> None:
        """Send a card (or just a result) to the client."""
        if card is None:
            rank, suit = 0, 0
        else:
            rank, suit = card.rank, card.suit
        
        pkt = struct.pack(
            SERVER_PAYLOAD_FMT,
            MAGIC_COOKIE,
            MessageType.PAYLOAD,
            result_code,
            rank,
            suit
        )
        conn.sendall(pkt)
    
    def _recv_decision(self, conn: socket.socket) -> str:
        """Receive player's decision (Hit or Stand)."""
        pkt = recv_exact(conn, CLIENT_PAYLOAD_LEN)
        cookie, mtype, decision = struct.unpack(CLIENT_PAYLOAD_FMT, pkt)
        
        if cookie != MAGIC_COOKIE or mtype != MessageType.PAYLOAD:
            raise ValueError("Invalid payload header from client")
        
        d = decision.decode("utf-8", errors="ignore")
        if d not in (DECISION_HIT, DECISION_STAND):
            raise ValueError(f"Invalid decision '{d}'")
        return d
    
    def _play_one_round(self, conn: socket.socket, client_name: str, round_index: int) -> int:
        """Play a single round of Blackjack. Returns the result code."""
        deck = fresh_shuffled_deck()
        
        # Initial deal
        player_cards = [deck.pop(), deck.pop()]
        dealer_cards = [deck.pop(), deck.pop()]
        
        player_sum = sum(card_value(c) for c in player_cards)
        dealer_visible_sum = card_value(dealer_cards[0])
        
        logger.info(f"\n--- Round {round_index} vs '{client_name}' ---")
        logger.info(f"Player: {card_str(player_cards[0])}, {card_str(player_cards[1])} (sum={player_sum})")
        logger.info(f"Dealer shows: {card_str(dealer_cards[0])} (visible={dealer_visible_sum})")
        
        # Send initial cards: player card 1, player card 2, dealer visible card
        self._send_card(conn, GameResult.NOT_OVER, player_cards[0])
        self._send_card(conn, GameResult.NOT_OVER, player_cards[1])
        self._send_card(conn, GameResult.NOT_OVER, dealer_cards[0])
        
        # Player turn
        while True:
            if player_sum > 21:
                logger.info(f"Player busts ({player_sum}) -> Dealer wins")
                self._send_card(conn, GameResult.LOSS, None)
                return GameResult.LOSS
            
            decision = self._recv_decision(conn)
            logger.info(f"Player decision: {decision}")
            
            if decision == DECISION_STAND:
                break
            
            # Hit - deal new card
            new_card = deck.pop()
            player_cards.append(new_card)
            player_sum += card_value(new_card)
            logger.info(f"Player hits: {card_str(new_card)} (sum={player_sum})")
            
            if player_sum > 21:
                self._send_card(conn, GameResult.LOSS, new_card)
                logger.info(f"Player busts -> Dealer wins")
                return GameResult.LOSS
            else:
                self._send_card(conn, GameResult.NOT_OVER, new_card)
        
        # Dealer turn (only if player didn't bust)
        dealer_sum = sum(card_value(c) for c in dealer_cards)
        logger.info(f"Dealer reveals: {card_str(dealer_cards[1])} (sum={dealer_sum})")
        self._send_card(conn, GameResult.NOT_OVER, dealer_cards[1])
        
        # Dealer must hit on 16 or less, stand on 17+
        while dealer_sum < 17:
            new_card = deck.pop()
            dealer_cards.append(new_card)
            dealer_sum += card_value(new_card)
            logger.info(f"Dealer hits: {card_str(new_card)} (sum={dealer_sum})")
            
            if dealer_sum > 21:
                self._send_card(conn, GameResult.WIN, new_card)
                logger.info(f"Dealer busts -> Player wins")
                return GameResult.WIN
            else:
                self._send_card(conn, GameResult.NOT_OVER, new_card)
        
        logger.info(f"Dealer stands ({dealer_sum})")
        
        # Determine winner
        if player_sum > dealer_sum:
            logger.info(f"Player {player_sum} > Dealer {dealer_sum} -> Player wins")
            self._send_card(conn, GameResult.WIN, None)
            return GameResult.WIN
        elif dealer_sum > player_sum:
            logger.info(f"Dealer {dealer_sum} > Player {player_sum} -> Dealer wins")
            self._send_card(conn, GameResult.LOSS, None)
            return GameResult.LOSS
        else:
            logger.info(f"Tie: Player {player_sum} == Dealer {dealer_sum}")
            self._send_card(conn, GameResult.TIE, None)
            return GameResult.TIE
    
    def _get_local_ip(self) -> str:
        """Get the local IP address of this machine."""
        try:
            # Create a dummy connection to find our IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            # Fallback methods
            try:
                return socket.gethostbyname(socket.gethostname())
            except Exception:
                return "0.0.0.0"
    
    def _get_broadcast_address(self) -> str:
        """Get the broadcast address for the local network."""
        try:
            local_ip = self._get_local_ip()
            # Simple /24 subnet assumption - use x.x.x.255
            parts = local_ip.split(".")
            if len(parts) == 4:
                return f"{parts[0]}.{parts[1]}.{parts[2]}.255"
        except Exception:
            pass
        return "<broadcast>"

# =========================
# Main entry point
# =========================
def main():
    """Start the Blackjack server."""
    TEAM_NAME = "Blackijecky"  # Change to your team name
    
    server = BlackjackServer(TEAM_NAME)
    server.start()

if __name__ == "__main__":
    main()