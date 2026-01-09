#!/usr/bin/env python3
"""
client.py - Blackjack client.
- Listens for UDP offers on 13122.
- Connects via TCP and plays N rounds.
- Supports manual decisions (Hit/Stand) and EXIT to quit.
- Adds card counting (Hi-Lo) and shows "hot/cold" deck status (True Count).
"""

from __future__ import annotations

import socket
import struct
import time
from typing import Tuple

from utils import (
    C, banner, color,
    MAGIC_COOKIE, UDP_LISTEN_PORT, CLIENT_TIMEOUT_SEC, OFFER_TIMEOUT_SEC,
    MessageType, GameResult,
    OFFER_FMT, OFFER_LEN,
    REQUEST_FMT, REQUEST_LEN,
    CLIENT_PAYLOAD_FMT, CLIENT_PAYLOAD_LEN,
    SERVER_PAYLOAD_FMT, SERVER_PAYLOAD_LEN,
    DECISION_HIT, DECISION_STAND,
    pad_name, unpad_name, recv_exact,
    card_str, card_value_rank
)


class BlackjackClient:
    def __init__(self, team_name: str):
        self.team_name = team_name

    # -------- Card counting (Hi-Lo) --------
    def hilo_delta(self, rank: int) -> int:
        # 2-6 = +1, 7-9 = 0, 10/J/Q/K/A = -1
        if 2 <= rank <= 6:
            return 1
        if 7 <= rank <= 9:
            return 0
        if rank == 1 or 10 <= rank <= 13:
            return -1
        return 0

    def pack_temperature(self, true_count: float) -> str:
        # Common thresholds
        if true_count >= 2.0:
            return color(f"🔥 HOT (TC={true_count:+.2f})", C.GREEN)
        if true_count <= -2.0:
            return color(f"🧊 COLD (TC={true_count:+.2f})", C.RED)
        return color(f"😐 NEUTRAL (TC={true_count:+.2f})", C.YELLOW)

    # -------- Networking --------
    def listen_for_offer(self) -> Tuple[str, int, str]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass

        sock.bind(("", UDP_LISTEN_PORT))
        sock.settimeout(OFFER_TIMEOUT_SEC)

        print(color(f"[CLIENT] Listening for offers on UDP port {UDP_LISTEN_PORT}...", C.YELLOW))

        while True:
            data, addr = sock.recvfrom(4096)
            if len(data) < OFFER_LEN:
                continue

            cookie, mtype, tcp_port, sname = struct.unpack(OFFER_FMT, data[:OFFER_LEN])
            if cookie != MAGIC_COOKIE or mtype != MessageType.OFFER:
                continue

            server_ip = addr[0]
            server_name = unpad_name(sname)
            sock.close()
            print(color(f"[CLIENT] Received offer from {server_ip} (server='{server_name}', port={tcp_port})", C.CYAN))
            return server_ip, int(tcp_port), server_name

    def send_request(self, conn: socket.socket, rounds: int) -> None:
        pkt = struct.pack(REQUEST_FMT, MAGIC_COOKIE, MessageType.REQUEST, int(rounds), pad_name(self.team_name))
        conn.sendall(pkt)
        # We DO NOT send '\n' to avoid breaking other teams.

    def send_decision(self, conn: socket.socket, decision: str) -> None:
        if decision not in (DECISION_HIT, DECISION_STAND):
            decision = DECISION_STAND
        pkt = struct.pack(CLIENT_PAYLOAD_FMT, MAGIC_COOKIE, MessageType.PAYLOAD, decision.encode("utf-8"))
        conn.sendall(pkt)

    def recv_server_payload(self, conn: socket.socket) -> Tuple[int, int, int]:
        pkt = recv_exact(conn, SERVER_PAYLOAD_LEN)
        cookie, mtype, result, rank, suit = struct.unpack(SERVER_PAYLOAD_FMT, pkt)
        if cookie != MAGIC_COOKIE or mtype != MessageType.PAYLOAD:
            raise ValueError("Invalid payload from server")
        return int(result), int(rank), int(suit)

    # -------- Input --------
    def ask_hit_or_stand(self, current_sum: int) -> str:
        while True:
            ans = input(color(f"Your sum is {current_sum}. Hit/Stand? [h/s] (or EXIT): ", C.BOLD)).strip().lower()
            if ans == "exit":
                return "EXIT"
            if ans.startswith("h"):
                return DECISION_HIT
            if ans.startswith("s"):
                return DECISION_STAND
            print(color("Please type 'h' for Hit, 's' for Stand, or 'EXIT'.", C.YELLOW))

    # -------- Game --------
    def play_round(
        self,
        conn: socket.socket,
        r: int,
        total: int,
        running_count: int,
        cards_seen_in_deck: int
    ):
        """
        Returns (GameResult, running_count, cards_seen_in_deck, round_cards_seen)
        cards_seen_in_deck is modulo 52 for a 1-deck shoe (server reshuffles only when empty).
        """
        print(color(f"\n[CLIENT] ===== Round {r}/{total} =====", C.MAGENTA))

        player_sum = 0
        round_cards_seen = 0

        def count_card(rank: int) -> None:
            nonlocal running_count, cards_seen_in_deck, round_cards_seen
            if rank != 0:
                running_count += self.hilo_delta(rank)
                cards_seen_in_deck += 1
                round_cards_seen += 1
                # server reshuffles only when deck is empty => every 52 seen we reset
                if cards_seen_in_deck >= 52:
                    cards_seen_in_deck = 0
                    running_count = 0

        # player card 1
        result, rank, suit = self.recv_server_payload(conn)
        count_card(rank)
        player_sum += card_value_rank(rank)
        print(color(f"[CLIENT] Your card: {card_str(rank, suit)} (sum={player_sum})", C.GREEN))

        # player card 2
        result, rank, suit = self.recv_server_payload(conn)
        count_card(rank)
        player_sum += card_value_rank(rank)
        print(color(f"[CLIENT] Your card: {card_str(rank, suit)} (sum={player_sum})", C.GREEN))

        # dealer visible
        result, rank, suit = self.recv_server_payload(conn)
        count_card(rank)
        dealer_visible_val = card_value_rank(rank)
        print(color(f"[CLIENT] Dealer shows: {card_str(rank, suit)}", C.BLUE))

        # player turn
        while player_sum <= 21:
            decision = self.ask_hit_or_stand(player_sum)
            if decision == "EXIT":
                return GameResult.NOT_OVER, running_count, cards_seen_in_deck, round_cards_seen

            self.send_decision(conn, decision)

            if decision == DECISION_STAND:
                break

            # receive new card
            result, rank, suit = self.recv_server_payload(conn)
            count_card(rank)
            if rank != 0:
                player_sum += card_value_rank(rank)
                print(color(f"[CLIENT] You drew: {card_str(rank, suit)} (sum={player_sum})", C.YELLOW))

            if result == GameResult.LOSS:
                print(color("[CLIENT] 💥 You busted! You lose this round.", C.RED))
                return GameResult.LOSS, running_count, cards_seen_in_deck, round_cards_seen

        # dealer turn + final result
        while True:
            result, rank, suit = self.recv_server_payload(conn)
            count_card(rank)

            if rank != 0:
                print(color(f"[CLIENT] Dealer card: {card_str(rank, suit)}", C.BLUE))

            if result != GameResult.NOT_OVER:
                break

        if result == GameResult.WIN:
            print(color("[CLIENT] 🎉 You win this round!", C.GREEN))
            return GameResult.WIN, running_count, cards_seen_in_deck, round_cards_seen
        if result == GameResult.LOSS:
            print(color("[CLIENT] 😢 You lose this round.", C.RED))
            return GameResult.LOSS, running_count, cards_seen_in_deck, round_cards_seen

        print(color("[CLIENT] 🤝 Tie.", C.YELLOW))
        return GameResult.TIE, running_count, cards_seen_in_deck, round_cards_seen

    def play_session(self, server_ip: str, tcp_port: int, rounds: int) -> Tuple[bool, int, int, int]:
        """
        Returns (completed_normally, wins, losses, ties)
        Card counting (Hi-Lo) persists across rounds (because server uses persistent shoe).
        """
        wins = losses = ties = 0

        running_count = 0
        cards_seen_in_deck = 0  # 0..51 in a single deck
        total_cards_seen = 0

        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conn.settimeout(CLIENT_TIMEOUT_SEC)
        conn.connect((server_ip, tcp_port))
        print(color(f"[CLIENT] Connected to server {server_ip}:{tcp_port}", C.CYAN))

        self.send_request(conn, rounds)

        for r in range(1, rounds + 1):
            res, running_count, cards_seen_in_deck, round_cards_seen = self.play_round(
                conn, r, rounds, running_count, cards_seen_in_deck
            )
            total_cards_seen += round_cards_seen

            # user requested EXIT mid-game
            if res == GameResult.NOT_OVER:
                print(color("[CLIENT] EXIT requested. Closing connection.", C.YELLOW))
                try:
                    conn.close()
                except Exception:
                    pass
                return (False, wins, losses, ties)

            if res == GameResult.WIN:
                wins += 1
            elif res == GameResult.LOSS:
                losses += 1
            else:
                ties += 1

            # True Count approximation for 1-deck shoe:
            # decks_remaining = remaining_cards/52, clamp to avoid weird spikes near end
            remaining_cards = 52 - cards_seen_in_deck
            decks_remaining = max(remaining_cards / 52.0, 0.25)
            true_count = running_count / decks_remaining

            print(color("[CLIENT] Count: ", C.BOLD) +
                  color(f"RC={running_count:+d}", C.CYAN) + "  " +
                  color(f"TC≈{true_count:+.2f}", C.CYAN) + "  " +
                  color(f"(cards seen in deck: {cards_seen_in_deck}/52)", C.GRAY))
            print(color("[CLIENT] Package status: ", C.BOLD) + self.pack_temperature(true_count))

        conn.close()
        return (True, wins, losses, ties)

    def client_loop(self) -> None:
        print(banner("🃏 BLACKJACK CLIENT 🃏"))
        print(color("[CLIENT] Type EXIT at the rounds prompt or during a round to quit.", C.GRAY))

        while True:
            rounds_str = input(color("\nHow many rounds to play? (1-255) or EXIT: ", C.BOLD)).strip()
            if rounds_str.lower() == "exit":
                print(color("[CLIENT] Goodbye 👋", C.YELLOW))
                return

            try:
                rounds = int(rounds_str)
                if not (1 <= rounds <= 255):
                    print(color("Please choose a number between 1 and 255.", C.YELLOW))
                    continue
            except ValueError:
                print(color("Invalid number. Please enter an integer or EXIT.", C.YELLOW))
                continue

            try:
                ip, port, _ = self.listen_for_offer()
                completed, wins, losses, ties = self.play_session(ip, port, rounds)

                total = wins + losses + ties
                win_rate = (wins / total * 100.0) if total else 0.0

                if completed:
                    print(color(f"\n[CLIENT] Finished playing {total} rounds, win rate: {win_rate:.1f}%", C.GREEN))
                else:
                    print(color(f"\n[CLIENT] Session aborted. Played so far: {total} rounds (W={wins} L={losses} T={ties})", C.YELLOW))

            except KeyboardInterrupt:
                print()
                print(color("[CLIENT] Goodbye 👋", C.YELLOW))
                return
            except socket.timeout:
                print(color("[CLIENT] Timeout waiting for server offer. Retrying...", C.YELLOW))
            except ConnectionError as e:
                print(color(f"[CLIENT] Connection error: {e}. Retrying...", C.YELLOW))
                time.sleep(1)
            except Exception as e:
                print(color(f"[CLIENT] Unexpected error: {e}. Retrying...", C.RED))
                time.sleep(1)
