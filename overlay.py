"""Floating dictation overlay: a frosted-glass pill near the bottom of the screen.

States:
  record  -> pill springs open, gradient bars react live to mic level (newest sound in
             the center, rippling outward)
  think   -> pill contracts, bars become a travelling shimmer wave while transcribing
  done    -> bars flash bright, pill collapses to a dot and fades
  error   -> bars turn red briefly, then the pill fades

All methods must be called on the main thread, except set_level() (any thread).
The panel never becomes key or active, so it can't steal focus from the app you're
dictating into.
"""

import math
import time
from collections import deque

import AppKit
import objc
from Foundation import NSMakeRect, NSRunLoop, NSRunLoopCommonModes, NSTimer
from PyObjCTools import AppHelper

PILL_H = 40.0
W_DOT, W_RECORD, W_THINK = 40.0, 196.0, 112.0
WIN_W, WIN_H = 320.0, 120.0
PILL_Y = 40.0                 # pill bottom inside the window (room for shadow + lift)
SCREEN_MARGIN = 22.0          # gap above the Dock / bottom of the visible frame

BAR_W, BAR_GAP = 3.0, 3.0
BAR_MIN_H, BAR_MAX_H = 3.0, 26.0
SIDE_PAD = 17.0

# Sky -> violet -> rose. Each bar takes its colour from its horizontal position.
PALETTE = [(0.30, 0.86, 1.00), (0.56, 0.50, 1.00), (1.00, 0.40, 0.72)]
ERROR_PALETTE = [(1.00, 0.52, 0.38), (1.00, 0.36, 0.36), (0.96, 0.30, 0.45)]


def _lerp(a, b, t):
    return a + (b - a) * t


def _clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def _palette_at(pal, x):
    x = _clamp(x) * (len(pal) - 1)
    i = min(int(x), len(pal) - 2)
    f = x - i
    return tuple(_lerp(pal[i][c], pal[i + 1][c], f) for c in range(3))


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


class _WaveView(AppKit.NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_WaveView, self).initWithFrame_(frame)
        if self is not None:
            self.painter = None
        return self

    def drawRect_(self, rect):
        if self.painter:
            self.painter(self.bounds())


def _pill_mask(h):
    r = h / 2.0

    def draw(rect):
        AppKit.NSColor.blackColor().set()
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, r, r).fill()
        return True

    img = AppKit.NSImage.imageWithSize_flipped_drawingHandler_((h + 2, h), False, draw)
    img.setCapInsets_(AppKit.NSEdgeInsets(r, r, r, r))
    img.setResizingMode_(AppKit.NSImageResizingModeStretch)
    return img


class Overlay:
    def __init__(self):
        style = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel
        win = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIN_W, WIN_H), style, AppKit.NSBackingStoreBuffered, False
        )
        win.setOpaque_(False)
        win.setBackgroundColor_(AppKit.NSColor.clearColor())
        win.setLevel_(AppKit.NSStatusWindowLevel)
        win.setIgnoresMouseEvents_(True)
        win.setHasShadow_(True)
        win.setHidesOnDeactivate_(False)
        win.setFloatingPanel_(True)
        win.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorIgnoresCycle
        )
        win.setAppearance_(AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua))
        self.window = win

        pill = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, PILL_Y, W_DOT, PILL_H))
        blur = AppKit.NSVisualEffectView.alloc().initWithFrame_(pill.bounds())
        blur.setMaterial_(AppKit.NSVisualEffectMaterialHUDWindow)
        blur.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        blur.setState_(AppKit.NSVisualEffectStateActive)
        blur.setMaskImage_(_pill_mask(PILL_H))
        blur.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        wave = _WaveView.alloc().initWithFrame_(pill.bounds())
        wave.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        wave.painter = self._paint
        pill.addSubview_(blur)
        pill.addSubview_(wave)
        win.contentView().addSubview_(pill)
        self.pill, self.wave = pill, wave

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
        self.last = time.perf_counter()
        self.timer = None
        self.token = 0

    # ------------------------------------------------------------ public API
    def set_level(self, rms: float):
        db = 20.0 * math.log10(rms + 1e-9)
        self.raw_level = _clamp((db + 52.0) / 36.0) ** 1.15

    def show_recording(self):
        self.token += 1
        if self.mode == "hidden":
            self._place_on_active_screen()
            self.width.snap(W_DOT)
            self.alpha.snap(0.0)
            self.lift.snap(-14.0)
            self.think.snap(0.0)
            self.error.snap(0.0)
            self.flash.snap(0.0)
            self.history.extend([0.0] * self.history.maxlen)
            self.level = self.raw_level = 0.0
            self.window.setAlphaValue_(0.0)
            self.window.orderFrontRegardless()
        self.mode = "record"
        self.width.target, self.alpha.target, self.lift.target = W_RECORD, 1.0, 0.0
        self.think.target = self.error.target = 0.0
        self._start_timer()

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
        AppHelper.callLater(1.3, lambda: self.token == token and self.hide(success=False))

    # ------------------------------------------------------------ internals
    def _place_on_active_screen(self):
        mouse = AppKit.NSEvent.mouseLocation()
        screen = next(
            (s for s in AppKit.NSScreen.screens() if AppKit.NSPointInRect(mouse, s.frame())),
            AppKit.NSScreen.mainScreen(),
        )
        vf = screen.visibleFrame()
        x = vf.origin.x + (vf.size.width - WIN_W) / 2.0
        y = vf.origin.y + SCREEN_MARGIN - PILL_Y
        self.window.setFrameOrigin_((round(x), round(y)))

    def _start_timer(self):
        if self.timer is None:
            self.last = time.perf_counter()
            self.timer = NSTimer.timerWithTimeInterval_repeats_block_(1 / 60, True, self._tick)
            NSRunLoop.mainRunLoop().addTimer_forMode_(self.timer, NSRunLoopCommonModes)

    def _tick(self, _timer):
        now = time.perf_counter()
        dt = min(now - self.last, 1 / 20)
        self.last = now
        self.t += dt

        target = self.raw_level if self.mode == "record" else 0.0
        rate = 28.0 if target > self.level else 9.0     # fast attack, soft release
        self.level += (target - self.level) * (1.0 - math.exp(-rate * dt))
        self.history.appendleft(self.level)

        moving = not self.width.settled
        for s in (self.width, self.alpha, self.lift, self.think, self.error, self.flash):
            s.step(dt)

        w = max(self.width.value, 8.0)
        self.pill.setFrame_(NSMakeRect((WIN_W - w) / 2.0, PILL_Y + self.lift.value, w, PILL_H))
        self.window.setAlphaValue_(_clamp(self.alpha.value))
        self.wave.setNeedsDisplay_(True)
        if moving:
            self.window.invalidateShadow()

        if self.mode == "hide" and self.alpha.value < 0.01 and self.alpha.settled:
            self.mode = "hidden"
            self.window.orderOut_(None)
            self.timer.invalidate()
            self.timer = None

    def _paint(self, bounds):
        w, h = bounds.size.width, bounds.size.height
        r = h / 2.0
        body = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, r, r)

        # Deep smoked glass: the blur shows through just enough to feel alive.
        AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.03, 0.74).set()
        body.fill()
        # Gentle vertical light falloff (lighter at the top).
        AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.0),
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.07),
        ).drawInBezierPath_angle_(body, 90.0)
        # Hairline rim, brighter along the top edge like light catching glass.
        rim = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSInsetRect(bounds, 0.5, 0.5), r - 0.5, r - 0.5
        )
        rim.setLineWidth_(1.0)
        AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.10).set()
        rim.stroke()
        AppKit.NSGraphicsContext.saveGraphicsState()
        AppKit.NSRectClip(NSMakeRect(0, h * 0.5, w, h * 0.5))
        AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.10).set()
        rim.stroke()
        AppKit.NSGraphicsContext.restoreGraphicsState()

        step = BAR_W + BAR_GAP
        n = int((w - 2 * SIDE_PAD + BAR_GAP) // step)
        if n < 1:
            return
        # Fade bars in once the pill has opened a bit, so they don't clip at the edges.
        bars_alpha = _clamp((w - W_DOT) / 30.0)
        if bars_alpha <= 0:
            return
        x0 = (w - (n * step - BAR_GAP)) / 2.0
        mid = (n - 1) / 2.0
        think = _clamp(self.think.value)
        err = _clamp(self.error.value)
        flash = _clamp(self.flash.value)
        hist, t = self.history, self.t
        glow = AppKit.NSShadow.alloc().init()
        glow.setShadowOffset_((0.0, 0.0))
        glow.setShadowBlurRadius_(7.0)

        for i in range(n):
            d = abs(i - mid)
            u = d / mid if mid > 0 else 0.0
            taper = 0.28 + 0.72 * math.cos(u * math.pi / 2.0) ** 1.3

            live = hist[min(int(d * 1.6), len(hist) - 1)] ** 0.8
            live *= taper * (0.84 + 0.16 * math.sin(t * 11.0 + i * 1.9))
            live = max(live, 0.03 + 0.025 * math.sin(t * 2.6 + i * 0.5))   # idle breath

            wave = 0.08 + 0.50 * (0.5 + 0.5 * math.sin(t * 6.2 - i * 0.62)) ** 3
            v = _clamp(_lerp(live, wave, think))
            bh = BAR_MIN_H + (BAR_MAX_H - BAR_MIN_H) * v

            pos = i / (n - 1) if n > 1 else 0.5
            if think > 0:  # shimmer: colour sweeps along with the wave
                pos = _lerp(pos, 0.5 + 0.5 * math.sin(t * 2.2 - i * 0.35), think * 0.6)
            rgb = [
                _lerp(a, b, err)
                for a, b in zip(_palette_at(PALETTE, pos), _palette_at(ERROR_PALETTE, pos))
            ]
            rgb = [_lerp(c, 1.0, flash * 0.9) for c in rgb]
            # Quiet bars sit back, loud ones light up.
            a = bars_alpha * (0.55 + 0.45 * _clamp(v * 2.4))

            glow.setShadowColor_(
                AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgb, 0.85 * a * (0.4 + v))
            )
            AppKit.NSGraphicsContext.saveGraphicsState()
            glow.set()
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgb, a).set()
            AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x0 + i * step, (h - bh) / 2.0, BAR_W, bh), BAR_W / 2.0, BAR_W / 2.0
            ).fill()
            AppKit.NSGraphicsContext.restoreGraphicsState()
