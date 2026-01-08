#!/usr/bin/env python3
from utils import *

class Client:
    def __init__(self):
        self.team_name = "Novi And Chino"
        self.rounds = None
        self.server_ip = None
        self.server_name = None
        self.tcp_port = None
        self.socket = None
        self.current_sum = 0
        self.round_num = 0

    def listen_for_offer(self):
        """Listen for server offer broadcasts and store server info."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Windows friendliness
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Allow multiple clients on same machine (where supported)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass

        sock.bind(("", UDP_LISTEN_PORT))
        sock.settimeout(OFFER_TIMEOUT_SEC)

        logger.info(f"Listening for offers on UDP port {UDP_LISTEN_PORT}...")

        while True:
            data, addr = sock.recvfrom(4096)
            if len(data) < OFFER_LEN:
                continue

            cookie, mtype, tcp_port, sname = struct.unpack(OFFER_FMT, data[:OFFER_LEN])
            if cookie != MAGIC_COOKIE or mtype != MessageType.OFFER:
                continue

            self.server_ip = addr[0]
            self.tcp_port = tcp_port
            self.server_name = unpad_name(sname)

            logger.info(f"Received offer from {self.server_ip} (server='{self.server_name}', port={self.tcp_port})")
            sock.close()
            return

    def send_request(self):
        pkt = struct.pack(
            REQUEST_FMT,
            MAGIC_COOKIE,
            MessageType.REQUEST,
            self.rounds,
            pad_name(self.team_name, 32)
        )
        self.socket.sendall(pkt)

    def send_decision(self, decision: str):
        if decision not in (DECISION_HIT, DECISION_STAND):
            decision = DECISION_STAND
        pkt = struct.pack(CLIENT_PAYLOAD_FMT, MAGIC_COOKIE, MessageType.PAYLOAD, decision.encode("utf-8"))
        self.socket.sendall(pkt)

    def recv_server_payload(self):
        pkt = recv_exact(self.socket, SERVER_PAYLOAD_LEN)
        cookie, mtype, result, rank, suit = struct.unpack(SERVER_PAYLOAD_FMT, pkt)
        if cookie != MAGIC_COOKIE or mtype != MessageType.PAYLOAD:
            raise ValueError("Invalid payload from server")
        return result, rank, suit

    def ask_user_hit_or_stand(self):
        while True:
            ans = input(f"Your sum is {self.current_sum}. Hit or Stand? [h/s]: ").strip().lower()
            if ans.startswith("h"):
                return DECISION_HIT
            if ans.startswith("s"):
                return DECISION_STAND
            print("Please type 'h' for Hit or 's' for Stand.")

    def play_round(self) -> GameResult:
        self.round_num += 1
        logger.info(f"===== Round {self.round_num}/{self.rounds} =====")

        self.current_sum = 0

        # Player card 1
        result, rank, suit = self.recv_server_payload()
        if rank != 0:
            self.current_sum += card_value(rank)
        logger.info(f"Your card: {card_str(rank, suit)} (sum={self.current_sum})")

        # Player card 2
        result, rank, suit = self.recv_server_payload()
        if rank != 0:
            self.current_sum += card_value(rank)
        logger.info(f"Your card: {card_str(rank, suit)} (sum={self.current_sum})")

        # Dealer visible card
        result, rank, suit = self.recv_server_payload()
        logger.info(f"Dealer shows: {card_str(rank, suit)}")

        # Player turn
        while self.current_sum <= 21:
            decision = self.ask_user_hit_or_stand()
            self.send_decision(decision)

            if decision == DECISION_STAND:
                break

            result, rank, suit = self.recv_server_payload()
            if rank != 0:
                self.current_sum += card_value(rank)
                logger.info(f"You drew: {card_str(rank, suit)} (sum={self.current_sum})")

            if result == GameResult.LOSS:
                logger.info("💥 You busted! You lose this round.")
                return GameResult.LOSS

        # Dealer turn + final result
        while True:
            result, rank, suit = self.recv_server_payload()
            if rank != 0:
                logger.info(f"Dealer card: {card_str(rank, suit)}")
            if result != GameResult.NOT_OVER:
                break

        if result == GameResult.WIN:
            logger.info("🎉 You win this round!")
            return GameResult.WIN
        elif result == GameResult.LOSS:
            logger.info("😢 You lose this round.")
            return GameResult.LOSS
        else:
            logger.info("🤝 Tie.")
            return GameResult.TIE

    def play_session(self) -> None:
        wins = losses = ties = 0

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(CLIENT_TIMEOUT_SEC)
        self.socket.connect((self.server_ip, self.tcp_port))
        logger.info(f"Connected to server {self.server_ip}:{self.tcp_port}")

        self.send_request()

        for _ in range(1, self.rounds + 1):
            print()
            result = self.play_round()
            if result == GameResult.WIN:
                wins += 1
            elif result == GameResult.LOSS:
                losses += 1
            else:
                ties += 1

        self.socket.close()

        total = wins + losses + ties
        win_rate = (wins / total) * 100 if total else 0.0

        print()
        logger.info("=" * 40)
        logger.info(f"Session complete! Played {total} rounds")
        logger.info(f"Results: W={wins} | L={losses} | T={ties}")
        logger.info(f"Win rate: {win_rate:.1f}%")
        logger.info("=" * 40)

    def play(self):
        print("\n" + "=" * 50)
        print("       🃏 BLACKJACK CLIENT 🃏")
        print("=" * 50)

        while True:
            try:
                rounds_input = input("\nHow many rounds to play? (1-255): ").strip()
                self.rounds = int(rounds_input)
                if not (1 <= self.rounds <= 255):
                    print("Please choose a number between 1 and 255")
                    continue
            except ValueError:
                print("Invalid number. Please enter a valid integer.")
                continue

            try:
                self.listen_for_offer()
                self.play_session()
            except KeyboardInterrupt:
                print()
                logger.info("Goodbye! 👋")
                break
            except socket.timeout:
                logger.info("Timeout waiting for server. Retrying...")
            except ConnectionError as e:
                logger.info(f"Connection error: {e}. Retrying...")
                time.sleep(1)
            except Exception as e:
                logger.error(f"Unexpected error: {e}. Retrying...")
                time.sleep(1)


if __name__ == "__main__":
    Client().play()
