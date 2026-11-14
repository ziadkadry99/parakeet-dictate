"""macOS renderer for the dictation pill: a frosted-glass NSPanel near the bottom of the screen.

Animation and state live in overlay_model.py. All methods must be called on the main thread,
except set_level() (any thread). The panel never becomes key or active, so it can't steal
focus from the app you're dictating into.
"""

import time

import AppKit
import objc
from Foundation import NSMakeRect, NSRunLoop, NSRunLoopCommonModes, NSTimer
from PyObjCTools import AppHelper

from .overlay_model import PILL_H, PILL_Y, SCREEN_MARGIN, W_DOT, WIN_H, WIN_W, OverlayModel


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
        self.model = OverlayModel(call_later=AppHelper.callLater)

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

        self.timer = None
        self.last = time.perf_counter()

    # ------------------------------------------------------------ public API
    def set_level(self, rms: float):
        self.model.set_level(rms)

    def show_recording(self):
        if self.model.show_recording():
            self._place_on_active_screen()
            self.window.setAlphaValue_(0.0)
            self.window.orderFrontRegardless()
        self._start_timer()

    def show_transcribing(self):
        self.model.show_transcribing()

    def hide(self, success=True):
        self.model.hide(success)

    def show_error(self):
        self.model.show_error()

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
        dt, self.last = now - self.last, now
        m = self.model
        visible = m.tick(dt)

        w = m.pill_width
        self.pill.setFrame_(NSMakeRect((WIN_W - w) / 2.0, PILL_Y + m.lift.value, w, PILL_H))
        self.window.setAlphaValue_(m.window_alpha)
        self.wave.setNeedsDisplay_(True)
        if m.width_moving:
            self.window.invalidateShadow()

        if not visible:
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

        glow = AppKit.NSShadow.alloc().init()
        glow.setShadowOffset_((0.0, 0.0))
        glow.setShadowBlurRadius_(7.0)
        for b in self.model.bars(w, h):
            glow.setShadowColor_(
                AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*b.rgb, b.glow_alpha)
            )
            AppKit.NSGraphicsContext.saveGraphicsState()
            glow.set()
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*b.rgb, b.alpha).set()
            AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(b.x, b.y, b.w, b.h), b.w / 2.0, b.w / 2.0
            ).fill()
            AppKit.NSGraphicsContext.restoreGraphicsState()
