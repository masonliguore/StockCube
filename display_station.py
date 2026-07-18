import datetime
import threading
import pandas as pd
import yfinance as yf
import mplfinance as mpf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw, ImageFont
import os
import pytz

import neopixel
import board

_GPIO_LOCK = threading.Lock()

# ── Framebuffer dimensions (physical, never changes) ─────────────────────────
PHYS_W, PHYS_H = 800, 480   # landscape framebuffer

# ── Logical canvas (portrait, before rotation) ────────────────────────────────
# We draw everything in portrait space (480×800), then rotate 90° CW to write
# to the 800×480 landscape framebuffer.
LOG_W, LOG_H = 480, 800

# ── UI bar heights in logical (portrait) space ────────────────────────────────
TIMEFRAME_BAR_H = 88
STOCK_BAR_H     = 112
UI_BARS_H       = TIMEFRAME_BAR_H + STOCK_BAR_H   # 200px
CHART_H         = LOG_H - UI_BARS_H               # 600px for chart

TIMEFRAME_BAR_Y = CHART_H                         # 600
STOCK_BAR_Y     = CHART_H + TIMEFRAME_BAR_H       # 688

# ── Robinhood colour palette ──────────────────────────────────────────────────
_DARK_BG  = "#111111"
_PANEL_BG = "#111111"
_GREEN    = "#00C805"
_RED      = "#FF5000"
_TEXT     = "#FFFFFF"
_GRID     = "#1a1a1a"
_AXIS     = "#333333"

# ── Timeframe definitions ─────────────────────────────────────────────────────
TIMEFRAMES = {
    "1D":  ("1d",  "5m"),
    "1W":  ("5d",  "30m"),
    "1M":  ("1mo", "1d"),
    "YTD": ("ytd", "1d"),
    "5Y":  ("5y",  "1wk"),
}


def _load_font(size):
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]:
        if os.path.exists(path):
            try: return ImageFont.truetype(path, size)
            except Exception: continue
    return ImageFont.load_default()


def _make_mpf_style():
    return mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=mpf.make_marketcolors(
            up=_GREEN, down=_RED,
            edge={"up": _GREEN, "down": _RED},
            wick={"up": _GREEN, "down": _RED},
            volume={"up": _GREEN, "down": _RED},
        ),
        facecolor=_PANEL_BG,
        edgecolor=_AXIS,
        figcolor=_DARK_BG,
        gridcolor=_GRID,
        gridstyle="--",
        gridaxis="both",
        y_on_right=True,
        rc={
            "axes.labelcolor": _TEXT,
            "xtick.color":     _TEXT,
            "ytick.color":     _TEXT,
            "text.color":      _TEXT,
            "figure.facecolor": _DARK_BG,
            "axes.facecolor":   _PANEL_BG,
        },
    )


def _calc_pct_change(data):
    """Return (pct_change, is_positive) for the dataset period."""
    try:
        open_price  = float(data["Open"].iloc[0])
        close_price = float(data["Close"].iloc[-1])
        if open_price == 0:
            return 0.0, True
        pct = ((close_price - open_price) / open_price) * 100
        return pct, pct >= 0
    except Exception:
        return 0.0, True


class DisplayStation:
    def __init__(self, framebuffer, neopixel_pin, led_count=60, stock_symbol="AAPL"):
        self.framebuffer        = framebuffer
        self.stock_symbol       = stock_symbol
        self.timeframe          = "1D"
        self.lock               = threading.Lock()
        self.on_chart_displayed = None   # set by TouchUI
        self._last_pct          = (0.0, True)  # (pct, is_positive)

        fb_id = os.path.basename(framebuffer)
        self.chart_path = f"/home/mliguore/stock_project/{fb_id}_chart.png"
        os.makedirs(os.path.dirname(self.chart_path), exist_ok=True)

        self.pixels = neopixel.NeoPixel(
            neopixel_pin, led_count,
            brightness=0.3, auto_write=True,
            pixel_order=neopixel.GRB,
        )
        self.pixels.fill((0, 0, 0))

    def set_led_status(self, color):
        with _GPIO_LOCK:
            self.pixels.fill(color)

    def market_is_closed(self):
        now     = datetime.datetime.now(tz=pytz.utc)
        eastern = pytz.timezone("US/Eastern")
        now_et  = now.astimezone(eastern)
        wd      = now_et.weekday()
        if wd >= 5: return True, "WEEKEND"
        mo  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
        mc  = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
        pmo = now_et.replace(hour=4,  minute=0,  second=0, microsecond=0)
        ahc = now_et.replace(hour=20, minute=0,  second=0, microsecond=0)
        if pmo <= now_et < mo:   return False, "PREMARKET"
        if mo  <= now_et <= mc:  return False, "REGULAR"
        if mc  < now_et <= ahc:  return False, "AFTERHOURS"
        return True, "CLOSED"

    def fetch_stock_data(self, closed=False):
        try:
            period, interval = TIMEFRAMES[self.timeframe]
            if closed and self.timeframe == "1D":
                period = "5d"

            data = yf.download(
                self.stock_symbol, period=period,
                interval=interval, progress=False, auto_adjust=True,
            )

            if data is None or data.empty:
                print(f"[{self.stock_symbol}] No data."); return None

            if isinstance(data.columns, pd.MultiIndex):
                ohlcv = {"Open", "High", "Low", "Close", "Volume"}
                keep  = 0
                for i in range(data.columns.nlevels):
                    if ohlcv.issubset(set(data.columns.get_level_values(i))):
                        keep = i; break
                data.columns = data.columns.get_level_values(keep)

            required = {"Open", "High", "Low", "Close", "Volume"}
            if not required.issubset(set(data.columns)):
                print(f"[{self.stock_symbol}] Missing columns."); return None

            data = data.dropna().astype(float)
            if data.index.tz is not None:
                data.index = data.index.tz_localize(None)
            if data.empty: return None

            if closed and self.timeframe == "1D":
                last_date = data.index[-1].date()
                data      = data.loc[[ts.date() == last_date for ts in data.index]]
                print(f"[{self.stock_symbol}] Last session: {last_date}")

            return data

        except Exception as e:
            print(f"[{self.stock_symbol}] Fetch error: {e}"); return None

    def render_chart(self, data, closed=False):
        if data is None or len(data) < 2:
            print(f"[{self.stock_symbol}] Not enough data."); return None

        # Compute and store percentage change for the UI overlay
        pct, is_pos = _calc_pct_change(data)
        self._last_pct = (pct, is_pos)
        print(f"[{self.stock_symbol}] {self.timeframe} change: "
              f"{'▲' if is_pos else '▼'}{abs(pct):.2f}%")

        closed_label = "  [CLOSED]" if closed else ""
        title        = f"{self.stock_symbol}  {self.timeframe}{closed_label}"

        try:
            style = _make_mpf_style()

            # Chart fills the logical portrait width (480px) × chart height (600px)
            fig_w = LOG_W  / 100   # 4.80 inches
            fig_h = CHART_H / 100  # 6.00 inches

            fig, axes = mpf.plot(
                data,
                type="candle",
                style=style,
                title=title,
                figsize=(fig_w, fig_h),
                returnfig=True,
                tight_layout=True,
            )
            fig.subplots_adjust(left=0.01, right=0.92, top=0.90, bottom=0.10)
            fig.savefig(
                self.chart_path, dpi=100,
                facecolor=_DARK_BG,
                bbox_inches="tight", pad_inches=0,
            )
            plt.close(fig)
            return self.chart_path

        except Exception as e:
            print(f"[{self.stock_symbol}] Render error: {e}"); return None

    def display_image(self, image_path):
        """
        Composite chart PNG onto the logical portrait canvas, then rotate
        90° CW and write to the landscape framebuffer.
        The TouchUI overlay (bars + pct) is composited by on_chart_displayed().
        """
        if not image_path or not os.path.exists(image_path):
            print(f"[{self.stock_symbol}] Image not found."); return

        try:
            # Build portrait canvas
            canvas = Image.new("RGBA", (LOG_W, LOG_H), (17, 17, 17, 255))

            # Paste chart into top portion
            chart = Image.open(image_path).convert("RGBA")
            chart = chart.resize((LOG_W, CHART_H), Image.LANCZOS)
            canvas.paste(chart, (0, 0))

            # Rotate 90° CW → landscape 800×480
            rotated = canvas.rotate(-90, expand=True)  # -90 = clockwise
            assert rotated.size == (PHYS_W, PHYS_H), \
                f"Rotated size {rotated.size} != ({PHYS_W},{PHYS_H})"

            # Write BGRA to framebuffer
            r, g, b, a = rotated.split()
            bgra = Image.merge("RGBA", (b, g, r, a))
            raw  = bgra.tobytes()

            expected = PHYS_W * PHYS_H * 4
            if len(raw) != expected:
                print(f"[{self.stock_symbol}] Size mismatch: {len(raw)} vs {expected}"); return

            with open(self.framebuffer, "wb") as f:
                f.write(raw)

            # Signal TouchUI to composite its bars on top
            if callable(self.on_chart_displayed):
                self.on_chart_displayed()

        except Exception as e:
            print(f"[{self.stock_symbol}] Display error: {e}")

    def update(self):
        with self.lock:
            closed, status = self.market_is_closed()
            print(f"[{self.stock_symbol}] [{self.timeframe}] {status}")

            self.set_led_status((0, 0, 255))
            data = self.fetch_stock_data(closed=closed)
            if data is None:
                self.set_led_status((255, 0, 0)); return

            self.set_led_status((255, 165, 0))
            chart_path = self.render_chart(data, closed=closed)
            if chart_path is None:
                self.set_led_status((255, 0, 0)); return

            self.set_led_status((0, 255, 0))
            self.display_image(chart_path)
            self.set_led_status((40, 40, 40) if closed else (0, 0, 0))

    def set_stock(self, symbol):
        print(f"[{self.framebuffer}] → {symbol}")
        self.stock_symbol = symbol
        self.update()

    def set_timeframe(self, tf):
        if tf not in TIMEFRAMES: return
        print(f"[{self.framebuffer}] TF → {tf}")
        self.timeframe = tf
        self.update()
