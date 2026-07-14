"""
main.py — Single-display stock visualisation station.

One DisplayStation drives /dev/fb0.
One TouchUI handles touch input on that display.
A main loop refreshes the display every UPDATE_INTERVAL seconds.
"""

# ── CRITICAL: These must be set before ANY other import, including board,
# display_station, and touch_ui — all of which may trigger pygame's SDL
# initialization the moment they are imported. SDL caches the video driver
# on first load; setting os.environ after that point has no effect. ──────────
import os
os.environ["SDL_VIDEODRIVER"] = "fbcon"
os.environ["SDL_FBDEV"]       = "/dev/fb0"
os.environ["SDL_NOMOUSE"]     = "1"
# ─────────────────────────────────────────────────────────────────────────────

import threading
import time
import signal
import sys

import board

from display_station import DisplayStation
from touch_ui import TouchUI

# ── Hardware config ────────────────────────────────────────────────────────────
FB       = "/dev/fb0"
LED_PIN  = board.D18
LED_COUNT = 60

STOCKS = ["AAPL", "TSLA", "MSFT", "NVDA"]

UPDATE_INTERVAL = 60  # seconds between automatic refreshes
# ───────────────────────────────────────────────────────────────────────────────


def main():
    stop_event = threading.Event()

    def handle_signal(sig, frame):
        print(f"\nReceived signal {sig} — shutting down.")
        stop_event.set()

    signal.signal(signal.SIGINT,  handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Build hardware objects
    station = DisplayStation(
        framebuffer=FB,
        neopixel_pin=LED_PIN,
        led_count=LED_COUNT,
        stock_symbol="AAPL",
    )

    # Initial render so the screen isn't blank on boot
    print("Running initial update...")
    station.update()

    # Start touch UI in its own thread
    ui = TouchUI(
        framebuffer=FB,
        station=station,
        stock_list=STOCKS,
    )
    ui_thread = threading.Thread(target=ui.start, name="UI", daemon=True)
    ui_thread.start()
    print("Touch UI started.")

    # Periodic update loop
    print("Update loop started.")
    while not stop_event.is_set():
        print("── Scheduled update ──")
        station.update()

        for _ in range(UPDATE_INTERVAL * 10):
            if stop_event.is_set():
                break
            time.sleep(0.1)

    print("Update loop stopped.")

    # Clean shutdown
    station.set_led_status((0, 0, 0))
    ui.stop()

    print("Goodbye.")
    sys.exit(0)


if __name__ == "__main__":
    main()
