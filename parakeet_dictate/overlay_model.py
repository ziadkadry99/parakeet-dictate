"""Toolkit-independent animation model for the dictation pill.

States:
  record  -> pill springs open, gradient bars react live to mic level (newest sound in
             the center, rippling outward)
  think   -> pill contracts, bars become a travelling shimmer wave while transcribing
  done    -> bars flash bright, pill collapses to a dot and fades
  error   -> bars turn red briefly, then the pill fades

A renderer (mac_overlay: AppKit, qt_overlay: Qt) owns the window, drives tick() at ~60 Hz
while visible and draws pill geometry + bars() each frame. All methods except set_level()
run on the UI thread.
"""

import math
from collections import deque
from dataclasses import dataclass

PILL_H = 40.0
W_DOT, W_RECORD, W_THINK = 40.0, 196.0, 112.0
WIN_W, WIN_H = 320.0, 120.0
PILL_Y = 40.0                 # pill bottom above the window's bottom edge (room for shadow + lift)
SCREEN_MARGIN = 22.0          # gap above the Dock / taskbar

BAR_W, BAR_GAP = 3.0, 3.0
BAR_MIN_H, BAR_MAX_H = 3.0, 26.0
SIDE_PAD = 17.0

# Sky -> violet -> rose. Each bar takes its colour from its horizontal position.
PALETTE = [(0.30, 0.86, 1.00), (0.56, 0.50, 1.00), (1.00, 0.40, 0.72)]
ERROR_PALETTE = [(1.00, 0.52, 0.38), (1.00, 0.36, 0.36), (0.96, 0.30, 0.45)]


def lerp(a, b, t):
    return a + (b - a) * t


def clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def _palette_at(pal, x):
    x = clamp(x) * (len(pal) - 1)
    i = min(int(x), len(pal) - 2)
    f = x - i
    return tuple(lerp(pal[i][c], pal[i + 1][c], f) for c in range(3))


class Spring:
    """Damped spring; damping_ratio < 1 gives a little overshoot."""

    def __init__(self, value, stiffness=340.0, damping_ratio=0.68):
        self.value = self.target = value
        self.vel = 0.0
        self.k = stiffness
        self.c = 2.0 * math.sqrt(stiffness) * damping_ratio

    def step(self, dt):
        n = int(dt / 0.004) + 1
        h = dt / n
        for _ in range(n):
            self.vel += (-self.k * (self.value - self.target) - self.c * self.vel) * h
            self.value += self.vel * h

    def snap(self, v):
        self.value = self.target = v
        self.vel = 0.0

    @property
    def settled(self):
        return abs(self.value - self.target) < 1e-3 and abs(self.vel) < 1e-2


@dataclass
class Bar:
    x: float          # left edge, in pill coordinates
    y: float          # offset from the pill's vertical edge (bars are centered, so the
                      # same value works for y-up and y-down coordinate systems)
    w: float
    h: float
    rgb: tuple
    alpha: float
    glow_alpha: float


class OverlayModel:
    def __init__(self, call_later):
        """call_later(seconds, fn): schedule fn on the UI thread."""
        self.call_later = call_later
        self.width = Spring(W_DOT, 360, 0.62)
        self.alpha = Spring(0.0, 260, 1.0)
        self.lift = Spring(0.0, 300, 0.7)
        self.think = Spring(0.0, 140, 1.0)    # 0 = live waveform, 1 = shimmer wave
        self.error = Spring(0.0, 160, 1.0)
        self.flash = Spring(0.0, 90, 1.0)

        self.mode = "hidden"
        self.raw_level = 0.0
        self.level = 0.0
        self.history = deque([0.0] * 64, maxlen=64)
        self.t = 0.0
        self.token = 0
        self.width_moving = False

    # ------------------------------------------------------------ state changes
    def set_level(self, rms: float):
        db = 20.0 * math.log10(rms + 1e-9)
        self.raw_level = clamp((db + 52.0) / 36.0) ** 1.15

    def show_recording(self) -> bool:
        """Returns True if the pill was hidden: the renderer must place + show its window."""
        self.token += 1
        appearing = self.mode == "hidden"
        if appearing:
            self.width.snap(W_DOT)
            self.alpha.snap(0.0)
            self.lift.snap(-14.0)
            self.think.snap(0.0)
            self.error.snap(0.0)
            self.flash.snap(0.0)
            self.history.extend([0.0] * self.history.maxlen)
            self.level = self.raw_level = 0.0
        self.mode = "record"
        self.width.target, self.alpha.target, self.lift.target = W_RECORD, 1.0, 0.0
        self.think.target = self.error.target = 0.0
        return appearing

    def show_transcribing(self):
        if self.mode == "hidden":
            return
        self.mode = "think"
        self.width.target = W_THINK
        self.think.target = 1.0

    def hide(self, success=True):
        if self.mode == "hidden":
            return
        self.mode = "hide"
        if success:
            self.flash.snap(1.0)
            self.flash.target = 0.0
        self.width.target, self.alpha.target, self.lift.target = W_DOT, 0.0, -6.0

    def show_error(self):
        if self.mode == "hidden":
            return
        self.mode = "error"
        self.think.target = 1.0
        self.error.target = 1.0
        self.width.target = W_THINK
        token = self.token
        self.call_later(1.3, lambda: self.token == token and self.hide(success=False))

    # ------------------------------------------------------------ animation
    def tick(self, dt) -> bool:
        """Advance by dt seconds. Returns False once fully faded out (renderer hides window)."""
        dt = min(dt, 1 / 20)
        self.t += dt

        target = self.raw_level if self.mode == "record" else 0.0
        rate = 28.0 if target > self.level else 9.0     # fast attack, soft release
        self.level += (target - self.level) * (1.0 - math.exp(-rate * dt))
        self.history.appendleft(self.level)

        self.width_moving = not self.width.settled
        for s in (self.width, self.alpha, self.lift, self.think, self.error, self.flash):
            s.step(dt)

        if self.mode == "hide" and self.alpha.value < 0.01 and self.alpha.settled:
            self.mode = "hidden"
            return False
        return True

    @property
    def pill_width(self) -> float:
        return max(self.width.value, 8.0)

    @property
    def window_alpha(self) -> float:
        return clamp(self.alpha.value)

    def bars(self, w: float, h: float) -> list[Bar]:
        step = BAR_W + BAR_GAP
        n = int((w - 2 * SIDE_PAD + BAR_GAP) // step)
        if n < 1:
            return []
        # Fade bars in once the pill has opened a bit, so they don't clip at the edges.
        bars_alpha = clamp((w - W_DOT) / 30.0)
        if bars_alpha <= 0:
            return []
        x0 = (w - (n * step - BAR_GAP)) / 2.0
        mid = (n - 1) / 2.0
        think = clamp(self.think.value)
        err = clamp(self.error.value)
        flash = clamp(self.flash.value)
        hist, t = self.history, self.t
        out = []

        for i in range(n):
            d = abs(i - mid)
            u = d / mid if mid > 0 else 0.0
            taper = 0.28 + 0.72 * math.cos(u * math.pi / 2.0) ** 1.3

            live = hist[min(int(d * 1.6), len(hist) - 1)] ** 0.8
            live *= taper * (0.84 + 0.16 * math.sin(t * 11.0 + i * 1.9))
            live = max(live, 0.03 + 0.025 * math.sin(t * 2.6 + i * 0.5))   # idle breath

            wave = 0.08 + 0.50 * (0.5 + 0.5 * math.sin(t * 6.2 - i * 0.62)) ** 3
            v = clamp(lerp(live, wave, think))
            bh = BAR_MIN_H + (BAR_MAX_H - BAR_MIN_H) * v

            pos = i / (n - 1) if n > 1 else 0.5
            if think > 0:  # shimmer: colour sweeps along with the wave
                pos = lerp(pos, 0.5 + 0.5 * math.sin(t * 2.2 - i * 0.35), think * 0.6)
            rgb = [
                lerp(a, b, err)
                for a, b in zip(_palette_at(PALETTE, pos), _palette_at(ERROR_PALETTE, pos))
            ]
            rgb = tuple(lerp(c, 1.0, flash * 0.9) for c in rgb)
            # Quiet bars sit back, loud ones light up.
            a = bars_alpha * (0.55 + 0.45 * clamp(v * 2.4))
            out.append(Bar(x0 + i * step, (h - bh) / 2.0, BAR_W, bh, rgb, a, 0.85 * a * (0.4 + v)))
        return out
