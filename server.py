from utils import *

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
        self.tcp_sock.settimeout(1.0)  # IMPORTANT: allow loop to exit cleanly
        self.tcp_port = self.tcp_sock.getsockname()[1]

        # UDP broadcast socket
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.udp_sock.bind(("", 0))
        except Exception:
            pass

        self.broadcast_addr = self._get_broadcast_address()

    def stop(self) -> None:
        """Stop the server."""
        self._cleanup()

    def start(self) -> None:
        """Start the server."""
        ip = self._get_local_ip()

        print("\n" + "=" * 50)
        print("       🃏 BLACKJACK SERVER 🃏")
        print("=" * 50)
        logger.info(f"Server started on IP {ip}, TCP port {self.tcp_port}")
        logger.info(f"Broadcasting offers on UDP port {UDP_LISTEN_PORT}")
        logger.info(f"Broadcast address: {self.broadcast_addr}")
        print("=" * 50 + "\n")

        offer_thread = threading.Thread(target=self._offer_loop, daemon=True)
        offer_thread.start()

        try:
            while self.running:
                try:
                    conn, addr = self.tcp_sock.accept()
                except socket.timeout:
                    continue  # check self.running again
                except OSError:
                    break  # socket closed

                conn.settimeout(CLIENT_TIMEOUT_SEC)
                logger.info(f"New connection from {addr[0]}:{addr[1]}")

                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(conn, addr),
                    daemon=True
                )
                client_thread.start()

        except KeyboardInterrupt:
            logger.info("Shutting down server...")
        finally:
            self._cleanup()

    def _cleanup(self) -> None:
        """Clean up server resources."""
        if not self.running:
            return
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

        # Robust targets:
        # - computed x.x.x.255
        # - global broadcast
        # - localhost (so single-process testing always works on Windows)
        targets = {self.broadcast_addr, "255.255.255.255", "127.0.0.1"}

        while self.running:
            for t in targets:
                try:
                    self.udp_sock.sendto(offer, (t, UDP_LISTEN_PORT))
                except Exception as e:
                    logger.warning(f"Offer broadcast error to {t}: {e}")
            time.sleep(OFFER_BROADCAST_INTERVAL_SEC)

    def _handle_client(self, conn: socket.socket, addr: Tuple[str, int]) -> None:
        """Handle a connected client through their game session."""
        try:
            req = recv_exact(conn, REQUEST_LEN)
            cookie, mtype, rounds, cname_bytes = struct.unpack(REQUEST_FMT, req)

            if cookie != MAGIC_COOKIE or mtype != MessageType.REQUEST:
                logger.warning(f"Invalid request from {addr}: protocol mismatch")
                conn.close()
                return

            client_name = unpad_name(cname_bytes)
            num_rounds = int(rounds)

            logger.info(f"Client '{client_name}' wants to play {num_rounds} rounds")

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
        deck = fresh_shuffled_deck()

        player_cards = [deck.pop(), deck.pop()]
        dealer_cards = [deck.pop(), deck.pop()]

        player_sum = sum(card_value(c) for c in player_cards)
        dealer_visible_sum = card_value(dealer_cards[0])

        logger.info(f"\n--- Round {round_index} vs '{client_name}' ---")
        logger.info(f"Player: {card_str(player_cards[0])}, {card_str(player_cards[1])} (sum={player_sum})")
        logger.info(f"Dealer shows: {card_str(dealer_cards[0])} (visible={dealer_visible_sum})")

        self._send_card(conn, GameResult.NOT_OVER, player_cards[0])
        self._send_card(conn, GameResult.NOT_OVER, player_cards[1])
        self._send_card(conn, GameResult.NOT_OVER, dealer_cards[0])

        while True:
            if player_sum > 21:
                logger.info(f"Player busts ({player_sum}) -> Dealer wins")
                self._send_card(conn, GameResult.LOSS, None)
                return GameResult.LOSS

            decision = self._recv_decision(conn)
            logger.info(f"Player decision: {decision}")

            if decision == DECISION_STAND:
                break

            new_card = deck.pop()
            player_cards.append(new_card)
            player_sum += card_value(new_card)
            logger.info(f"Player hits: {card_str(new_card)} (sum={player_sum})")

            if player_sum > 21:
                self._send_card(conn, GameResult.LOSS, new_card)
                logger.info("Player busts -> Dealer wins")
                return GameResult.LOSS
            else:
                self._send_card(conn, GameResult.NOT_OVER, new_card)

        dealer_sum = sum(card_value(c) for c in dealer_cards)
        logger.info(f"Dealer reveals: {card_str(dealer_cards[1])} (sum={dealer_sum})")
        self._send_card(conn, GameResult.NOT_OVER, dealer_cards[1])

        while dealer_sum < 17:
            new_card = deck.pop()
            dealer_cards.append(new_card)
            dealer_sum += card_value(new_card)
            logger.info(f"Dealer hits: {card_str(new_card)} (sum={dealer_sum})")

            if dealer_sum > 21:
                self._send_card(conn, GameResult.WIN, new_card)
                logger.info("Dealer busts -> Player wins")
                return GameResult.WIN
            else:
                self._send_card(conn, GameResult.NOT_OVER, new_card)

        logger.info(f"Dealer stands ({dealer_sum})")

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

    def _get_broadcast_address(self) -> str:
        try:
            local_ip = self._get_local_ip()
            parts = local_ip.split(".")
            if len(parts) == 4:
                return f"{parts[0]}.{parts[1]}.{parts[2]}.255"
        except Exception:
            pass
        return "<broadcast>"
