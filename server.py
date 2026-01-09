#!/usr/bin/env python3
"""
server.py - Blackjack dealer/server.
- Broadcasts UDP offers every second.
- Accepts TCP connections and runs N rounds per client.
- Uses a persistent 1-deck "shoe" per client connection so card counting makes sense.
- Uses the exact protocol formats from the assignment.
"""

from __future__ import annotations

import random
import socket
import struct
import threading
import time
from typing import List, Optional, Tuple

from utils import (
    C, banner, color,
    MAGIC_COOKIE, UDP_LISTEN_PORT, OFFER_BROADCAST_INTERVAL_SEC, CLIENT_TIMEOUT_SEC,
    MessageType, GameResult,
    OFFER_FMT,
    REQUEST_FMT, REQUEST_LEN,
    CLIENT_PAYLOAD_FMT, CLIENT_PAYLOAD_LEN,
    SERVER_PAYLOAD_FMT,
    pad_name, unpad_name, recv_exact, maybe_consume_newline,
    Card, card_value, card_str, get_local_ip, guess_broadcast_address
)


def fresh_shuffled_deck() -> List[Card]:
    deck = [Card(rank=r, suit=s) for s in range(4) for r in range(1, 14)]
    random.shuffle(deck)
    return deck


class Shoe:
    """
    1-deck shoe per client connection.
    IMPORTANT: reshuffles only when deck is empty.
    That makes client-side card counting consistent (no hidden reshuffles mid-deck).
    """
    def __init__(self):
        self.deck: List[Card] = fresh_shuffled_deck()

    def reshuffle(self) -> None:
        self.deck = fresh_shuffled_deck()

    def draw(self) -> Card:
        if len(self.deck) == 0:
            self.reshuffle()
        return self.deck.pop()


class BlackjackServer:
    def __init__(self, team_name: str):
        self.team_name = team_name
        self.team_name_bytes = pad_name(team_name)
        self.running = threading.Event()
        self.running.set()

        # TCP server (any free port)
        self.tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp_sock.bind(("", 0))
        self.tcp_sock.listen()
        self.tcp_port = self.tcp_sock.getsockname()[1]

        # UDP broadcaster
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        self.local_ip = get_local_ip()
        self.broadcast_addr = guess_broadcast_address(self.local_ip)

    def start(self) -> None:
        print(banner("🃏 BLACKJACK SERVER 🃏"))
        print(color(f"[SERVER] Server started, listening on IP address {self.local_ip}", C.GREEN))
        print(color(f"[SERVER] TCP port: {self.tcp_port}", C.GREEN))
        print(color(f"[SERVER] Broadcasting offers on UDP port {UDP_LISTEN_PORT} every {OFFER_BROADCAST_INTERVAL_SEC}s", C.YELLOW))
        print(color(f"[SERVER] Broadcast address: {self.broadcast_addr}", C.YELLOW))
        print(color("[SERVER] Press Ctrl+C to stop the server.", C.GRAY))

        t_offer = threading.Thread(target=self._offer_loop, daemon=True)
        t_offer.start()

        try:
            while self.running.is_set():
                conn, addr = self.tcp_sock.accept()
                conn.settimeout(CLIENT_TIMEOUT_SEC)
                print(color(f"[SERVER] New connection from {addr[0]}:{addr[1]}", C.CYAN))

                t = threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True)
                t.start()

        except KeyboardInterrupt:
            print()
            print(color("[SERVER] Shutting down...", C.RED))
        finally:
            self.stop()

    def stop(self) -> None:
        self.running.clear()
        try:
            self.tcp_sock.close()
        except Exception:
            pass
        try:
            self.udp_sock.close()
        except Exception:
            pass

    def _offer_loop(self) -> None:
        offer = struct.pack(
            OFFER_FMT,
            MAGIC_COOKIE,
            MessageType.OFFER,
            self.tcp_port,
            self.team_name_bytes
        )

        while self.running.is_set():
            try:
                self.udp_sock.sendto(offer, (self.broadcast_addr, UDP_LISTEN_PORT))
                self.udp_sock.sendto(offer, ("<broadcast>", UDP_LISTEN_PORT))
            except Exception:
                pass
            time.sleep(OFFER_BROADCAST_INTERVAL_SEC)

    def _handle_client(self, conn: socket.socket, addr: Tuple[str, int]) -> None:
        try:
            req = recv_exact(conn, REQUEST_LEN)
            cookie, mtype, rounds, cname_bytes = struct.unpack(REQUEST_FMT, req)

            if cookie != MAGIC_COOKIE or mtype != MessageType.REQUEST:
                print(color(f"[SERVER] Invalid request from {addr}: bad cookie/type", C.RED))
                conn.close()
                return

            # tolerate optional newline after request
            maybe_consume_newline(conn)

            client_name = unpad_name(cname_bytes)
            num_rounds = int(rounds)

            print(color(f"[SERVER] Client '{client_name}' requested {num_rounds} rounds", C.MAGENTA))

            # Persistent shoe for this connection (card counting)
            shoe = Shoe()

            wins = losses = ties = 0
            for r in range(1, num_rounds + 1):
                result = self._play_one_round(conn, client_name, r, num_rounds, shoe)
                if result == GameResult.WIN:
                    wins += 1
                elif result == GameResult.LOSS:
                    losses += 1
                else:
                    ties += 1

            print(color(f"[SERVER] Session ended for '{client_name}': W={wins} L={losses} T={ties}", C.GREEN))
            conn.close()

        except (ConnectionError, socket.timeout):
            print(color(f"[SERVER] Client {addr} disconnected/timed out", C.YELLOW))
            self._safe_close(conn)
        except Exception as e:
            print(color(f"[SERVER] Error with client {addr}: {e}", C.RED))
            self._safe_close(conn)

    def _safe_close(self, conn: socket.socket) -> None:
        try:
            conn.close()
        except Exception:
            pass

    def _send_payload(self, conn: socket.socket, result: int, card: Optional[Card]) -> None:
        if card is None:
            rank, suit = 0, 0
        else:
            rank, suit = card.rank, card.suit

        pkt = struct.pack(
            SERVER_PAYLOAD_FMT,
            MAGIC_COOKIE,
            MessageType.PAYLOAD,
            int(result),
            int(rank),
            int(suit),
        )
        conn.sendall(pkt)

    def _recv_decision(self, conn: socket.socket) -> str:
        pkt = recv_exact(conn, CLIENT_PAYLOAD_LEN)
        cookie, mtype, decision_bytes = struct.unpack(CLIENT_PAYLOAD_FMT, pkt)

        if cookie != MAGIC_COOKIE or mtype != MessageType.PAYLOAD:
            raise ValueError("Invalid client payload header")

        d = decision_bytes.decode("utf-8", errors="ignore")
        if d not in ("Hittt", "Stand"):
            raise ValueError(f"Invalid decision '{d}'")
        return d

    def _play_one_round(self, conn: socket.socket, client_name: str, r: int, total: int, shoe: Shoe) -> int:
        player_cards = [shoe.draw(), shoe.draw()]
        dealer_cards = [shoe.draw(), shoe.draw()]

        player_sum = sum(card_value(c) for c in player_cards)
        dealer_sum = sum(card_value(c) for c in dealer_cards)

        print(color(f"\n[SERVER] --- Round {r}/{total} vs '{client_name}' ---", C.CYAN))
        print(color(f"[SERVER] Player: {card_str(player_cards[0].rank, player_cards[0].suit)} "
                    f"{card_str(player_cards[1].rank, player_cards[1].suit)}  (sum={player_sum})", C.GRAY))
        print(color(f"[SERVER] Dealer shows: {card_str(dealer_cards[0].rank, dealer_cards[0].suit)}", C.GRAY))

        # initial deal to client: player1, player2, dealer visible
        self._send_payload(conn, GameResult.NOT_OVER, player_cards[0])
        self._send_payload(conn, GameResult.NOT_OVER, player_cards[1])
        self._send_payload(conn, GameResult.NOT_OVER, dealer_cards[0])

        # Player turn
        while True:
            if player_sum > 21:
                print(color(f"[SERVER] Player busts ({player_sum}) -> Dealer wins", C.RED))
                self._send_payload(conn, GameResult.LOSS, None)
                return GameResult.LOSS

            decision = self._recv_decision(conn)
            print(color(f"[SERVER] Player decision: {decision}", C.MAGENTA))

            if decision == "Stand":
                break

            new_card = shoe.draw()
            player_cards.append(new_card)
            player_sum += card_value(new_card)
            print(color(f"[SERVER] Player hits: {card_str(new_card.rank, new_card.suit)} (sum={player_sum})", C.YELLOW))

            if player_sum > 21:
                self._send_payload(conn, GameResult.LOSS, new_card)
                print(color("[SERVER] Player busts -> Dealer wins", C.RED))
                return GameResult.LOSS
            else:
                self._send_payload(conn, GameResult.NOT_OVER, new_card)

        # Dealer turn
        print(color(f"[SERVER] Dealer reveals: {card_str(dealer_cards[1].rank, dealer_cards[1].suit)} (sum={dealer_sum})", C.BLUE))
        self._send_payload(conn, GameResult.NOT_OVER, dealer_cards[1])

        while dealer_sum < 17:
            new_card = shoe.draw()
            dealer_cards.append(new_card)
            dealer_sum += card_value(new_card)
            print(color(f"[SERVER] Dealer hits: {card_str(new_card.rank, new_card.suit)} (sum={dealer_sum})", C.BLUE))

            if dealer_sum > 21:
                self._send_payload(conn, GameResult.WIN, new_card)
                print(color("[SERVER] Dealer busts -> Player wins", C.GREEN))
                return GameResult.WIN
            else:
                self._send_payload(conn, GameResult.NOT_OVER, new_card)

        print(color(f"[SERVER] Dealer stands (sum={dealer_sum})", C.BLUE))

        if player_sum > dealer_sum:
            self._send_payload(conn, GameResult.WIN, None)
            print(color(f"[SERVER] Player {player_sum} > Dealer {dealer_sum} -> Player wins", C.GREEN))
            return GameResult.WIN
        if dealer_sum > player_sum:
            self._send_payload(conn, GameResult.LOSS, None)
            print(color(f"[SERVER] Dealer {dealer_sum} > Player {player_sum} -> Dealer wins", C.RED))
            return GameResult.LOSS

        self._send_payload(conn, GameResult.TIE, None)
        print(color(f"[SERVER] Tie: {player_sum} == {dealer_sum}", C.YELLOW))
        return GameResult.TIE
