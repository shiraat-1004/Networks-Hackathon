from server import BlackjackServer
from client import Client
import threading
import time

def main():
    TEAM_NAME = "Blackijecky"  # your server team name

    server = BlackjackServer(TEAM_NAME)

    # Start server in background thread
    t = threading.Thread(target=server.start, daemon=True)
    t.start()

    # Give the server a moment to start + begin broadcasting
    time.sleep(0.5)

    # Run client in foreground
    try:
        client = Client()
        client.play()
    finally:
        # Stop server when client exits
        server.stop()
        time.sleep(0.2)

if __name__ == "__main__":
    main()
