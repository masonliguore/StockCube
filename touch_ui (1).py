"""
touch_ui.py — Robinhood-style portrait UI with 90° CW rotation.

Logical canvas: 480×800 portrait
  ┌───────────────────┐  ↑
  │                   │  │
  │   Chart  480×600  │  600px
  │                   │  │
  ├───────────────────┤  ↓
  │ 1D  1W  1M  YTD 5Y│  88px  ← timeframe bar
  ├───────────────────┤
  │AAPL TSLA MSFT  ★+│  112px ← stock bar (includes pct display)
  └───────────────────┘

Written to framebuffer after 90° CW rotation → 800×480 landscape.
Touch (x,y) from evdev is un-rotated to logical coords before hit-testing.
"""

import os
import json
import threading
import time
import struct
import glob
from PIL import Image, ImageDraw, ImageFont

# ── Dimensions ────────────────────────────────────────────────────────────────
PHYS_W, PHYS_H = 800, 480   # landscape framebuffer
LOG_W,  LOG_H  = 480, 800   # logical portrait canvas

# ── Bar geometry (must match display_station.py) ──────────────────────────────
TIMEFRAME_BAR_H = 88
STOCK_BAR_H     = 112
UI_BARS_H       = TIMEFRAME_BAR_H + STOCK_BAR_H
CHART_H         = LOG_H - UI_BARS_H   # 600

TIMEFRAME_BAR_Y = CHART_H             # 600
STOCK_BAR_Y     = CHART_H + TIMEFRAME_BAR_H  # 688

# ── Robinhood colours ─────────────────────────────────────────────────────────
C_BG        = (17,  17,  17,  255)
C_BAR       = (22,  22,  22,  255)
C_BAR_TF    = (28,  28,  28,  255)
C_GREEN     = (0,   200, 5,   255)
C_RED       = (255, 80,  0,   255)
C_WHITE     = (255, 255, 255, 255)
C_GREY      = (110, 110, 110, 255)
C_BORDER    = (45,  45,  45,  255)
C_BORDER_ACT= (0,   200, 5,   255)
C_FAV_ON    = (255, 210, 0,   255)
C_FAV_OFF   = (70,  70,  70,  255)
C_KB_BG     = (10,  10,  10,  235)
C_KB_KEY    = (38,  38,  38,  255)
C_KB_OK     = (0,   155, 5,   255)
C_KB_DEL    = (90,  20,  20,  255)

# ── Timeframe bar ─────────────────────────────────────────────────────────────
TF_LABELS   = ["1D", "1W", "1M", "YTD", "5Y"]
TF_BTN_H    = 52
TF_BTN_W    = 74
TF_BTN_GAP  = 10
TF_BTN_MARG = (TIMEFRAME_BAR_H - TF_BTN_H) // 2

# ── Stock bar ─────────────────────────────────────────────────────────────────
SK_ROW_H    = 56          # stock buttons occupy top row of stock bar
SK_PCT_H    = STOCK_BAR_H - SK_ROW_H   # 56px for the pct / price row
SK_BTN_H    = 40
SK_BTN_MARG = (SK_ROW_H - SK_BTN_H) // 2
SK_BTN_MIN  = 84
SK_BTN_GAP  = 8
SK_BTN_PAD  = 12
FAV_W       = 48
ADD_W       = 48

# ── Keyboard ──────────────────────────────────────────────────────────────────
KB_ROWS  = [list("QWERTYUIOP"), list("ASDFGHJKL"), list("ZXCVBNM")]
KB_KEY_W = 40
KB_KEY_H = 50
KB_GAP   = 5

# ── Favorites ─────────────────────────────────────────────────────────────────
FAVORITES_PATH    = "/home/mliguore/stock_project/favorites.json"
DEFAULT_FAVORITES = ["AAPL", "TSLA", "MSFT", "NVDA"]

# ── evdev ─────────────────────────────────────────────────────────────────────
EV_SYN, EV_ABS, EV_KEY = 0, 3, 1
ABS_X, ABS_Y            = 0, 1
ABS_MT_POSITION_X       = 53
ABS_MT_POSITION_Y       = 54
BTN_TOUCH_CODE          = 330
EVENT_SIZE              = 24
EVENT_FORMAT            = "llHHi"


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _find_touch_device():
    for dev_path in sorted(glob.glob("/dev/input/event*")):
        try:
            np = f"/sys/class/input/{os.path.basename(dev_path)}/device/name"
            if os.path.exists(np):
                name = open(np).read().strip().lower()
                if any(k in name for k in ("touch", "ft5", "goodix", "ili")):
                    print(f"[TouchUI] Touch: {dev_path} ({name})")
                    return dev_path
        except Exception: continue
    devs = sorted(glob.glob("/dev/input/event*"))
    return devs[0] if devs else None


def _load_favorites():
    if os.path.exists(FAVORITES_PATH):
        try:
            d = json.load(open(FAVORITES_PATH))
            if isinstance(d, list) and d: return d
        except Exception: pass
    return list(DEFAULT_FAVORITES)


def _save_favorites(favs):
    try:
        os.makedirs(os.path.dirname(FAVORITES_PATH), exist_ok=True)
        json.dump(favs, open(FAVORITES_PATH, "w"), indent=2)
    except Exception as e:
        print(f"[TouchUI] Save error: {e}")


def _hit(x, y, r):
    return r[0] <= x <= r[2] and r[1] <= y <= r[3]


def _rrect(draw, rect, fill, outline=None, radius=8, width=1):
    x1, y1, x2, y2 = rect
    draw.rounded_rectangle([x1, y1, x2, y2], radius=radius,
                           fill=fill, outline=outline, width=width)


def _ctext(draw, rect, text, font, color):
    bb = draw.textbbox((0, 0), text, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    tx = rect[0] + (rect[2] - rect[0] - tw) // 2
    ty = rect[1] + (rect[3] - rect[1] - th) // 2
    draw.text((tx, ty), text, font=font, fill=color)


def _rotate_touch(raw_x, raw_y, x_max, y_max):
    """
    Convert raw evdev coordinates to logical portrait canvas coordinates.

    The screen hardware reports in landscape orientation (x: 0→800, y: 0→480).
    After 90° CW rotation, landscape (rx, ry) maps to portrait (lx, ly) as:
        lx = ry  (landscape y becomes portrait x)
        ly = x_max - rx  (landscape x becomes inverted portrait y)
    We scale to logical canvas dimensions.
    """
    lx = int(raw_y * LOG_W / y_max)
    ly = int((x_max - raw_x) * LOG_H / x_max)
    return lx, ly


class TouchUI:
    MODE_NORMAL   = "normal"
    MODE_KEYBOARD = "keyboard"

    def __init__(self, framebuffer, station, stock_list=None):
        self.framebuffer    = framebuffer
        self.station        = station
        self.favorites      = _load_favorites()
        self.selected_stock = station.stock_symbol
        self.selected_tf    = station.timeframe
        self._running       = False
        self._update_thread = None
        self._mode          = self.MODE_NORMAL
        self._kb_input      = ""
        self._last_chart    = None   # RGBA, logical portrait size (LOG_W × CHART_H)

        self.font_tf    = _load_font(20)
        self.font_sk    = _load_font(19)
        self.font_pct   = _load_font(28)
        self.font_kb    = _load_font(20)
        self.font_inp   = _load_font(24)

        station.on_chart_displayed = self._on_chart_displayed

        self._tf_rects = {}
        self._sk_rects = {}
        self._kb_rects = {}
        self._rebuild_tf_bar()
        self._rebuild_sk_bar()
        self._build_keyboard()

    # ── Public ────────────────────────────────────────────────────────────────

    def start(self):
        self._running = True
        self._redraw()
        touch_dev = _find_touch_device()
        if touch_dev is None:
            print("[TouchUI] No touch device — display only.")
            while self._running: time.sleep(0.5)
            return
        self._event_loop(touch_dev)

    def stop(self):
        self._running = False

    # ── Layout builders ───────────────────────────────────────────────────────

    def _rebuild_tf_bar(self):
        rects   = {}
        total_w = len(TF_LABELS) * TF_BTN_W + (len(TF_LABELS) - 1) * TF_BTN_GAP
        start_x = (LOG_W - total_w) // 2
        y1 = TIMEFRAME_BAR_Y + TF_BTN_MARG
        y2 = y1 + TF_BTN_H
        for i, label in enumerate(TF_LABELS):
            x1 = start_x + i * (TF_BTN_W + TF_BTN_GAP)
            rects[label] = (x1, y1, x1 + TF_BTN_W, y2)
        self._tf_rects = rects

    def _rebuild_sk_bar(self):
        rects = {}
        right_reserved = FAV_W + SK_BTN_GAP + ADD_W + SK_BTN_GAP
        y1 = STOCK_BAR_Y + SK_BTN_MARG
        y2 = y1 + SK_BTN_H
        x  = SK_BTN_GAP
        for sym in self.favorites:
            w  = max(SK_BTN_MIN, len(sym) * 12 + SK_BTN_PAD * 2)
            x2 = x + w
            if x2 > LOG_W - right_reserved: break
            rects[sym] = (x, y1, x2, y2)
            x = x2 + SK_BTN_GAP
        fav_x = LOG_W - right_reserved
        rects["__FAV__"] = (fav_x, y1, fav_x + FAV_W, y2)
        add_x = LOG_W - ADD_W - SK_BTN_GAP
        rects["__ADD__"] = (add_x, y1, add_x + ADD_W, y2)
        self._sk_rects = rects

    def _build_keyboard(self):
        rects    = {}
        kb_top_y = 130
        for ri, row in enumerate(KB_ROWS):
            row_w   = len(row) * (KB_KEY_W + KB_GAP) - KB_GAP
            start_x = (LOG_W - row_w) // 2
            y1      = kb_top_y + ri * (KB_KEY_H + KB_GAP)
            y2      = y1 + KB_KEY_H
            for ci, key in enumerate(row):
                x1 = start_x + ci * (KB_KEY_W + KB_GAP)
                rects[key] = (x1, y1, x1 + KB_KEY_W, y2)
        act_y1 = kb_top_y + 3 * (KB_KEY_H + KB_GAP)
        act_y2 = act_y1 + KB_KEY_H
        rects["⌫"]  = (SK_BTN_GAP,          act_y1, SK_BTN_GAP + 90,         act_y2)
        rects["OK"] = (LOG_W - 100 - SK_BTN_GAP, act_y1, LOG_W - SK_BTN_GAP, act_y2)
        rects["✕"]  = ((LOG_W - 80) // 2,   act_y1, (LOG_W + 80) // 2,       act_y2)
        self._kb_rects = rects

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _redraw(self):
        """Build portrait canvas, rotate 90° CW, write to framebuffer."""
        canvas = Image.new("RGBA", (LOG_W, LOG_H), C_BG)

        # Paste cached chart into top of canvas
        if self._last_chart is not None:
            chart = self._last_chart.resize((LOG_W, CHART_H), Image.LANCZOS)
            canvas.paste(chart, (0, 0))

        draw = ImageDraw.Draw(canvas, "RGBA")

        if self._mode == self.MODE_NORMAL:
            self._draw_tf_bar(draw)
            self._draw_sk_bar(draw)
        else:
            self._draw_keyboard(draw)

        # Rotate 90° CW: portrait (LOG_W×LOG_H) → landscape (PHYS_W×PHYS_H)
        rotated = canvas.rotate(-90, expand=True)

        # Write BGRA to framebuffer
        r, g, b, a = rotated.split()
        bgra = Image.merge("RGBA", (b, g, r, a))
        raw  = bgra.tobytes()
        try:
            with open(self.framebuffer, "wb") as f:
                f.write(raw)
        except Exception as e:
            print(f"[TouchUI] FB write error: {e}")

    def _draw_tf_bar(self, draw):
        # Background
        draw.rectangle([0, TIMEFRAME_BAR_Y, LOG_W, TIMEFRAME_BAR_Y + TIMEFRAME_BAR_H],
                       fill=C_BAR_TF)
        draw.line([(0, TIMEFRAME_BAR_Y), (LOG_W, TIMEFRAME_BAR_Y)],
                  fill=C_BORDER, width=1)

        for label, rect in self._tf_rects.items():
            active = (label == self.selected_tf)
            _rrect(draw, rect,
                   fill=C_BG,
                   outline=C_BORDER_ACT if active else C_BORDER,
                   radius=8, width=2 if active else 1)
            _ctext(draw, rect, label, self.font_tf,
                   C_GREEN if active else C_GREY)

    def _draw_sk_bar(self, draw):
        # Background
        draw.rectangle([0, STOCK_BAR_Y, LOG_W, LOG_H], fill=C_BAR)
        draw.line([(0, STOCK_BAR_Y), (LOG_W, STOCK_BAR_Y)],
                  fill=C_BORDER, width=1)

        # Stock buttons (top row of stock bar)
        for label, rect in self._sk_rects.items():
            if label == "__FAV__":
                is_fav  = self.selected_stock in self.favorites
                outline = C_FAV_ON if is_fav else C_BORDER
                text    = "★"
                tcolor  = C_FAV_ON if is_fav else C_FAV_OFF
            elif label == "__ADD__":
                outline = C_GREEN
                text    = "+"
                tcolor  = C_GREEN
            else:
                active  = (label == self.selected_stock)
                outline = C_GREEN if active else C_BORDER
                text    = label
                tcolor  = C_GREEN if active else C_WHITE
            _rrect(draw, rect, fill=C_BG, outline=outline, radius=8, width=2)
            _ctext(draw, rect, text, self.font_sk, tcolor)

        # Percentage change row (bottom portion of stock bar)
        pct_y  = STOCK_BAR_Y + SK_ROW_H
        pct, is_pos = self.station._last_pct
        sign   = "▲" if is_pos else "▼"
        color  = C_GREEN if is_pos else C_RED
        pct_str = f"{sign} {abs(pct):.2f}%  {self.selected_tf}"

        bb = draw.textbbox((0, 0), pct_str, font=self.font_pct)
        tw = bb[2] - bb[0]
        th = bb[3] - bb[1]
        tx = (LOG_W - tw) // 2
        ty = pct_y + (SK_PCT_H - th) // 2
        draw.text((tx, ty), pct_str, font=self.font_pct, fill=color)

    def _draw_keyboard(self, draw):
        draw.rectangle([0, 0, LOG_W, LOG_H], fill=C_KB_BG)

        # Input field
        inp_rect = (SK_BTN_GAP, 60, LOG_W - SK_BTN_GAP, 118)
        _rrect(draw, inp_rect, fill=(22, 22, 22, 255), outline=C_GREEN,
               radius=8, width=2)
        text   = self._kb_input or "Type ticker..."
        tcolor = C_WHITE if self._kb_input else C_GREY
        bb     = draw.textbbox((0, 0), text, font=self.font_inp)
        th     = bb[3] - bb[1]
        draw.text((SK_BTN_GAP + 14, 60 + (58 - th) // 2),
                  text, font=self.font_inp, fill=tcolor)

        for key, rect in self._kb_rects.items():
            if key == "OK":
                fill, tcolor = C_KB_OK, C_WHITE
            elif key in ("⌫", "✕"):
                fill, tcolor = C_KB_DEL, C_WHITE
            else:
                fill, tcolor = C_KB_KEY, C_WHITE
            _rrect(draw, rect, fill=fill, outline=C_BORDER, radius=5, width=1)
            _ctext(draw, rect, key, self.font_kb, tcolor)

    # ── Station callback ──────────────────────────────────────────────────────

    def _on_chart_displayed(self):
        """
        Called after display_station writes a new chart to the framebuffer.
        Read back the chart portion, un-rotate it, cache it, then redraw UI.
        """
        try:
            # Framebuffer holds a 90° CW-rotated landscape image (BGRA).
            # Read the full frame and un-rotate to get the portrait canvas back.
            raw  = open(self.framebuffer, "rb").read(PHYS_W * PHYS_H * 4)
            bgra = Image.frombytes("RGBA", (PHYS_W, PHYS_H), raw)
            b, g, r, a = bgra.split()
            rgba = Image.merge("RGBA", (r, g, b, a))

            # Un-rotate: rotate 90° CCW (= -90° CW) to get portrait back
            portrait = rgba.rotate(90, expand=True)   # CCW = undo CW rotation
            # Crop just the chart area (top LOG_W × CHART_H of portrait canvas)
            self._last_chart = portrait.crop((0, 0, LOG_W, CHART_H))

        except Exception:
            self._last_chart = Image.new("RGBA", (LOG_W, CHART_H), C_BG)

        self._redraw()

    # ── Touch event loop ──────────────────────────────────────────────────────

    def _event_loop(self, touch_dev):
        raw_x = raw_y = 0
        touch_down = False
        x_max = self._axis_max(touch_dev, 0, PHYS_W)
        y_max = self._axis_max(touch_dev, 1, PHYS_H)
        print(f"[TouchUI] Listening on {touch_dev} (raw max {x_max}×{y_max})")

        try:
            with open(touch_dev, "rb") as f:
                while self._running:
                    try:
                        data = f.read(EVENT_SIZE)
                        if len(data) < EVENT_SIZE: continue
                        _, _, et, ec, ev = struct.unpack(EVENT_FORMAT, data)

                        if et == EV_ABS:
                            if ec in (ABS_X, ABS_MT_POSITION_X):
                                raw_x = ev
                            elif ec in (ABS_Y, ABS_MT_POSITION_Y):
                                raw_y = ev
                        elif et == EV_KEY and ec == BTN_TOUCH_CODE:
                            touch_down = (ev == 1)
                        elif et == EV_SYN and touch_down:
                            # Convert raw hardware coords → logical portrait coords
                            lx, ly = _rotate_touch(raw_x, raw_y, x_max, y_max)
                            self._handle_tap(lx, ly)
                            touch_down = False
                    except Exception:
                        continue
        except PermissionError:
            print(f"[TouchUI] Permission denied: {touch_dev}. sudo usermod -aG input $USER")
        except Exception as e:
            print(f"[TouchUI] Event loop error: {e}")

    def _axis_max(self, touch_dev, axis_idx, fallback):
        try:
            name = os.path.basename(touch_dev)
            path = f"/sys/class/input/{name}/device/absmax"
            if os.path.exists(path):
                v = int(open(path).read().split()[axis_idx])
                return v if v > 0 else fallback
        except Exception: pass
        return fallback

    def _handle_tap(self, x, y):
        if self._mode == self.MODE_NORMAL:
            self._tap_normal(x, y)
        else:
            self._tap_keyboard(x, y)

    def _tap_normal(self, x, y):
        for label, rect in self._tf_rects.items():
            if _hit(x, y, rect):
                self._select_timeframe(label); return
        for label, rect in self._sk_rects.items():
            if _hit(x, y, rect):
                if   label == "__ADD__": self._open_keyboard()
                elif label == "__FAV__": self._toggle_favorite()
                else:                    self._select_stock(label)
                return

    def _tap_keyboard(self, x, y):
        for key, rect in self._kb_rects.items():
            if _hit(x, y, rect):
                if   key == "OK": self._confirm_ticker()
                elif key == "✕":  self._close_keyboard()
                elif key == "⌫":
                    self._kb_input = self._kb_input[:-1]; self._redraw()
                elif len(self._kb_input) < 10:
                    self._kb_input += key; self._redraw()
                return

    # ── Actions ───────────────────────────────────────────────────────────────

    def _select_stock(self, symbol):
        if symbol == self.selected_stock: return
        if self._update_thread and self._update_thread.is_alive():
            print(f"[TouchUI] Busy, ignoring {symbol}"); return
        print(f"[TouchUI] Stock → {symbol}")
        self.selected_stock = symbol
        self._redraw()
        self._update_thread = threading.Thread(
            target=self.station.set_stock, args=(symbol,), daemon=True)
        self._update_thread.start()

    def _select_timeframe(self, tf):
        if tf == self.selected_tf: return
        if self._update_thread and self._update_thread.is_alive():
            print(f"[TouchUI] Busy, ignoring TF {tf}"); return
        print(f"[TouchUI] TF → {tf}")
        self.selected_tf = tf
        self._redraw()
        self._update_thread = threading.Thread(
            target=self.station.set_timeframe, args=(tf,), daemon=True)
        self._update_thread.start()

    def _toggle_favorite(self):
        sym = self.selected_stock
        if sym in self.favorites: self.favorites.remove(sym)
        else:                     self.favorites.append(sym)
        _save_favorites(self.favorites)
        self._rebuild_sk_bar()
        self._redraw()

    def _open_keyboard(self):
        self._kb_input = ""
        self._mode     = self.MODE_KEYBOARD
        self._redraw()

    def _close_keyboard(self):
        self._mode     = self.MODE_NORMAL
        self._kb_input = ""
        self._redraw()

    def _confirm_ticker(self):
        ticker = self._kb_input.strip().upper()
        if not ticker: self._close_keyboard(); return
        print(f"[TouchUI] New ticker: {ticker}")
        self._close_keyboard()
        self.selected_stock = ticker
        if ticker not in self.favorites:
            self.favorites.append(ticker)
            _save_favorites(self.favorites)
            self._rebuild_sk_bar()
        if self._update_thread and self._update_thread.is_alive(): return
        self._update_thread = threading.Thread(
            target=self.station.set_stock, args=(ticker,), daemon=True)
        self._update_thread.start()
