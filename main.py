#!/usr/bin/env python3
from __future__ import annotations

import threading
import time

from utils import enable_ansi_on_windows, banner, color, C
from server import BlackjackServer
from client import BlackjackClient


def main():
    enable_ansi_on_windows()

    TEAM_NAME = "Novi&Chinno"

    print(banner("🃏 BLACKIJECKY 🃏"))
    print(color("[MAIN] Starting server and client automatically", C.CYAN))
    print(color("[MAIN] You can always run server.py and client.py separately if required", C.GRAY))
    ans = input(color("[MAIN] Press c to run client separately , s to run server separately or ENTER to run both:", C.YELLOW))
    if ans == "s":
        # Start server in background thread
        server = BlackjackServer(TEAM_NAME)
        server_thread = threading.Thread(target=server.start, daemon=True)
        server_thread.start()
        time.sleep(0.4)
        input("Type EXIT to stop server.")

    elif ans == "c":
        client = BlackjackClient(TEAM_NAME)
        client.client_loop()
    else:

        # Start server in background thread
        server = BlackjackServer(TEAM_NAME)
        server_thread = threading.Thread(target=server.start, daemon=True)
        server_thread.start()

        # Give server time to bind sockets and start broadcasting offers
        time.sleep(0.4)

        # Start client in foreground
        client = BlackjackClient(TEAM_NAME)
        try:
            client.client_loop()
        finally:
            # If client exits, stop server as well (same process)
            server.stop()
            print(color("[MAIN] Server stopped. Goodbye 👋", C.YELLOW))


if __name__ == "__main__":
    main()
