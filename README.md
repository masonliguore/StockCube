# Stock Cube

A real-time stock market display station built on a Raspberry Pi 5, featuring a 5-inch DSI touchscreen, WS2812E LED status indicators, live candlestick charts, and a custom touch UI — all running headless on the Linux framebuffer with no desktop environment required.

---

## Overview

This project turns a Raspberry Pi 5 into a dedicated stock market terminal. It fetches live OHLCV data from Yahoo Finance, renders Robinhood-style candlestick charts using mplfinance, and displays them directly to a DSI framebuffer at 60 fps. A PIL-based touch UI overlaid on the chart allows the user to switch between stocks and timeframes, manage a favorites list, and enter any Yahoo Finance ticker symbol via an on-screen keyboard — all without a mouse, keyboard, or desktop environment.

A WS2812E LED strip provides real-time visual status feedback: blue during data fetch, orange during render, green during display, and dim white when the market is closed.

The system is fully market-hours aware, automatically detecting pre-market, regular, after-hours, and closed/weekend states, and falls back to the most recent trading session's data when the market is not open.

---

## Features

- **Live candlestick charts** rendered with mplfinance in a Robinhood-inspired dark theme
- **Five timeframes:** 1D, 1W, 1M, YTD, 5Y — switchable via touch
- **Percentage gain/loss** displayed for the current period
- **Favorites system** persisted to JSON — survives reboots
- **On-screen QWERTY keyboard** for entering any Yahoo Finance ticker
- **WS2812E LED status strip** with color-coded states
- **Market-hours awareness** — pre-market, regular, after-hours, closed, weekend
- **Headless framebuffer rendering** — no X11, Wayland, or desktop environment
- **Direct evdev touch input** — no SDL, pygame, or display server required
- **90° portrait rotation** — landscape framebuffer driven in portrait orientation
- **Graceful shutdown** on SIGINT/SIGTERM with LED cleanup

---

## Hardware

| Component | Description |
|-----------|-------------|
| [Raspberry Pi 5](https://www.raspberrypi.com/products/raspberry-pi-5/) | 4GB or 8GB recommended |
| [Hosyond 5" DSI Touchscreen](https://www.amazon.com/dp/B0CXTFN8K9) | 800×480, DSI interface with capacitive touch |
| WS2812E LED Strip | 60 LEDs, 5V, data on GPIO 18 (Pin 12) |
| 45W USB-C Power Supply | 5V/5A — required for Pi 5 + display + LEDs |
| MicroSD Card | 16GB+ recommended, Class 10 or better |
| DSI Ribbon Cable | Included with display |
| Jumper Wires | For LED strip connection |

### GPIO Wiring

| Signal | GPIO | Physical Pin |
|--------|------|-------------|
| LED Data | GPIO 18 | Pin 12 |
| LED Power | 5V | Pin 2 or Pin 4 |
| LED Ground | GND | Pin 6, 9, or 14 |

> **Note:** WS2812E LEDs require a 5V power line. For strips longer than ~30 LEDs, power the strip from an external 5V supply sharing a common ground with the Pi rather than drawing from the Pi's 5V pins.

---

## Software Architecture

```
stock_project/
├── main.py               # Entry point — initializes station and UI, runs update loop
├── display_station.py    # Data fetching, chart rendering, framebuffer output, LED control
├── touch_ui.py           # PIL-based touch UI overlay, evdev input, favorites management
└── favorites.json        # Auto-generated — persists favorite ticker symbols
```

### Module Responsibilities

**`display_station.py`**
- Fetches OHLCV data via `yfinance` with multi-timeframe support
- Renders candlestick charts with `mplfinance` using a custom dark theme
- Writes chart output directly to `/dev/fb0` in BGRA format
- Rotates the logical portrait canvas 90° CW before writing to the landscape framebuffer
- Controls WS2812E LEDs via Adafruit NeoPixel + lgpio
- Handles market-hours detection and last-session fallback when closed

**`touch_ui.py`**
- Draws the persistent UI overlay (timeframe bar, stock bar, percentage display) using PIL
- Composites the overlay on top of the chart image after each render
- Reads raw evdev touch events from `/dev/input/eventX` — no SDL or X11 dependency
- Translates hardware touch coordinates to logical portrait canvas coordinates
- Manages the favorites list and on-screen keyboard

**`main.py`**
- Initializes `DisplayStation` and `TouchUI` instances
- Runs the initial chart render before the UI thread starts
- Runs a 60-second periodic update loop in the main thread
- Handles SIGINT/SIGTERM for graceful shutdown

---

## OS & System Setup

### 1. Flash Raspberry Pi OS

Flash **Raspberry Pi OS Bookworm (64-bit)** to your SD card using [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Enable SSH in the imager settings if you plan to work headlessly.

### 2. Enable DSI Display

Edit `/boot/firmware/config.txt`:

```bash
sudo nano /boot/firmware/config.txt
```

Ensure the following lines are present:

```ini
display_auto_detect=1
max_framebuffers=2
dtoverlay=vc4-kms-v3d
disable_fw_kms_setup=1
dtparam=i2c_arm=on
dtparam=spi=on
```

### 3. Set Boot to Console (Required)

The program writes directly to the framebuffer and requires no desktop environment. Configure the Pi to boot to console:

```bash
sudo raspi-config
```

Navigate to **System Options → Boot / Auto Login → Console Autologin** and select it. This prevents Labwc/Wayland from starting and claiming the display.

### 4. Add User to Required Groups

```bash
sudo usermod -aG video,gpio,input [YOUR USERNAME]
```

Log out and back in (or reboot) for group changes to take effect:

```bash
sudo reboot
```

Verify after reboot:

```bash
groups
# Should include: video gpio input
```

### 5. Install System Dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
    python3-pip \
    python3-venv \
    python3-lgpio \
    python3-pygame \
    i2c-tools \
    libatlas-base-dev \
    libopenblas-dev \
    fonts-dejavu-core
```

---

## Python Environment Setup

### 1. Create Virtual Environment

```bash
python3 -m venv /home/[YOUR USERNAME]/myenv --system-site-packages
source /home/[YOUR USERNAME]/myenv/bin/activate
```

> The `--system-site-packages` flag is required so the venv can access system-level packages like `lgpio` and `python3-lgpio` that cannot be installed cleanly via pip on Pi OS Bookworm.

### 2. Install Python Dependencies

```bash
pip install \
    yfinance \
    mplfinance \
    matplotlib \
    pandas \
    Pillow \
    adafruit-circuitpython-neopixel \
    adafruit-blinka \
    evdev
```

### 3. Clone the Project

```bash
git clone https://github.com/masonliguore/StockCube /home/[YOUR USERNAME]/stock_project
cd /home/[YOUR USERNAME]/stock_project
```

---

## Running the Program

### Stop the Desktop (if running)

```bash
systemctl --user stop labwc
```

### Activate the Environment and Run

```bash
source /home/[YOUR USERNAME]/myenv/bin/activate
python3 /home/[YOUR USERNAME]/stock_project/main.py
```

### Stop the Program

Press `Ctrl+C` — the program handles SIGINT cleanly, turns off the LEDs, and exits.

---

## Autostart on Boot (Optional)

To have the program launch automatically after boot, create a systemd service:

```bash
sudo tee /etc/systemd/system/stock-station.service << 'EOF'
[Unit]
Description=Stock Visualization Station
After=multi-user.target

[Service]
Type=simple
User=[YOUR USERNAME]
WorkingDirectory=/home/[YOUR USERNAME]/stock_project
ExecStart=/home/[YOUR USERNAME]/myenv/bin/python3 /home/[YOUR USERNAME]/stock_project/main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable stock-station.service
sudo systemctl start stock-station.service
```

Check status:

```bash
sudo systemctl status stock-station.service
```

View live logs:

```bash
journalctl -u stock-station.service -f
```

---

## Usage

### Touch Controls

| Action | Result |
|--------|--------|
| Tap a ticker button | Switch to that stock |
| Tap **1D / 1W / 1M / YTD / 5Y** | Change chart timeframe |
| Tap **★** | Favorite or unfavorite the current stock |
| Tap **+** | Open on-screen keyboard to enter any ticker |
| Type ticker + **OK** | Display that stock and add to favorites |
| Tap **✕** | Close keyboard without changing stock |

### LED Status Reference

| Color | Meaning |
|-------|---------|
| 🔵 Blue | Fetching data from Yahoo Finance |
| 🟠 Orange | Rendering candlestick chart |
| 🟢 Green | Writing chart to display |
| ⚪ Dim white | Market closed — showing last session |
| ⚫ Off | Market open — idle between updates |
| 🔴 Red | Fetch or render error |

---

## Troubleshooting

**Display shows nothing after running the program**
Ensure Labwc/Wayland is not running (`systemctl --user stop labwc`). The program requires exclusive framebuffer access.

**Permission denied on `/dev/fb0`**
Your user is not in the `video` group. Run `sudo usermod -aG video [YOUR USERNAME]` and reboot.

**Permission denied on `/dev/input/eventX`**
Your user is not in the `input` group. Run `sudo usermod -aG input [YOUR USERNAME]` and reboot.

**LEDs not lighting up**
Verify GPIO 18 (Pin 12) is connected to the strip's DIN pin. Ensure the strip's power line is connected to a 5V source (Pin 2 or 4) and GND is shared with the Pi. Run the LED test:
```bash
python3 -c "
import board, neopixel, time
p = neopixel.NeoPixel(board.D18, 60, brightness=0.3, auto_write=True, pixel_order=neopixel.GRB)
p.fill((255, 0, 0)); time.sleep(2); p.fill((0, 0, 0))
"
```

**`yfinance` returns no data**
The market may be closed and the 1D period returned nothing. The program automatically falls back to the last trading session. If data is still missing, check your internet connection.

**Touch input not registering**
Run `ls /dev/input/event*` and `cat /sys/class/input/eventX/device/name` to confirm the touchscreen is detected. Ensure the DSI ribbon cable is fully seated.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `yfinance` | Yahoo Finance OHLCV data |
| `mplfinance` | Candlestick chart rendering |
| `matplotlib` | Figure backend for mplfinance |
| `pandas` | Data manipulation |
| `Pillow` | UI drawing and framebuffer compositing |
| `adafruit-circuitpython-neopixel` | WS2812E LED control |
| `adafruit-blinka` | CircuitPython compatibility layer |
| `evdev` | Raw touch input via Linux input subsystem |
| `lgpio` | GPIO access on Raspberry Pi 5 (system package) |

---

## License

MIT License. See `LICENSE` for details.

---

## Author

Mason Liguore
