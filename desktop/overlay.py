"""Aria 浮动覆盖层 — 类 Siri 式对话视图。

使用 AppKit (pyobjc) 创建原生 macOS 浮动窗口。
通过 NSTimer 在主线程更新 UI。
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Optional

import AppKit
import Foundation

logger = logging.getLogger("desktop.overlay")

# ── 颜色 ────────────────────────────────────────────────────────

ACCENT = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.2, 0.6, 0.7, 1.0)
BG = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.08, 0.10, 0.15, 0.88)
TEXT = AppKit.NSColor.whiteColor()
DIM = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.55, 0.55, 0.62, 1.0)


# ── 窗口代理: 处理动画定时器 ────────────────────────────────────

class OverlayDelegate(Foundation.NSObject):
    """处理波形动画定时器的 ObjC 代理。"""

    def initWithBars_(self, bars):
        import objc
        self = objc.super(OverlayDelegate, self).init()
        if self:
            self._bars = bars
            self._phase = 0.0
            self._timer = None
            self._running = False
        return self

    def startAnimation(self):
        """启动波形动画。"""
        self.stopAnimation()
        self._phase = 0.0
        self._running = True
        self._timer = Foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1 / 30, self, self._updateWave_, None, True
        )
        Foundation.NSRunLoop.currentRunLoop().addTimer_forMode_(
            self._timer, Foundation.NSRunLoopCommonModes
        )

    def stopAnimation(self):
        """停止波形动画。"""
        self._running = False
        if self._timer:
            self._timer.invalidate()
            self._timer = None
        for bar in self._bars:
            bar.setFrameSize_(Foundation.NSMakeSize(4, 4))
            bar.setAlphaValue_(0.5)

    def _updateWave_(self, timer):
        """NSTimer 回调: 更新波形。"""
        if not self._running:
            return
        self._phase += 0.15
        for i, bar in enumerate(self._bars):
            h = abs(math.sin(self._phase + i * 0.4)) * 22 + 4
            bar.setFrameSize_(Foundation.NSMakeSize(4, h))


# ── 覆盖窗口 ────────────────────────────────────────────────────

class OverlayWindow:
    """浮动覆盖窗口 — 右上角弹出对话视图。"""

    W = 380
    H_LISTEN = 120
    H_RESPONSE = 280

    def __init__(self):
        self._window: Optional[AppKit.NSWindow] = None
        self._text: Optional[AppKit.NSTextField] = None
        self._subtext: Optional[AppKit.NSTextField] = None
        self._waves: list[AppKit.NSView] = []
        self._delegate: Optional[OverlayDelegate] = None

    def _build(self):
        """懒创建窗口 (必须在主线程调用)。"""
        if self._window:
            return

        screen = AppKit.NSScreen.mainScreen()
        if not screen:
            return
        frame = screen.frame()
        x = frame.size.width - self.W - 20
        y = frame.size.height - self.H_LISTEN - 60

        self._window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            Foundation.NSMakeRect(x, y, self.W, self.H_LISTEN),
            AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        w = self._window
        w.setOpaque_(False)
        w.setBackgroundColor_(AppKit.NSColor.clearColor())
        w.setLevel_(AppKit.NSFloatingWindowLevel)
        w.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
            AppKit.NSWindowCollectionBehaviorStationary |
            AppKit.NSWindowCollectionBehaviorIgnoresCycle
        )
        w.setTitleVisibility_(AppKit.NSWindowTitleHidden)
        w.setTitlebarAppearsTransparent_(True)
        w.setHasShadow_(True)

        cv = w.contentView()
        cv.setWantsLayer_(True)
        cv.layer().setCornerRadius_(16)
        cv.layer().setMasksToBounds_(True)
        # Use rgba directly
        cv.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.08, 0.10, 0.15, 0.88
            ).CGColor()
        )

        # 文字
        self._text = self._label("🎤 聆听中...", 20, 40, self.W - 40, 30, 18, bold=True)
        cv.addSubview_(self._text)

        self._subtext = self._label("", 20, 12, self.W - 40, 22, 13, color=DIM)
        self._subtext.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        cv.addSubview_(self._subtext)

        # 波形条
        self._build_waves(cv)

        # 动画代理
        self._delegate = OverlayDelegate.alloc().initWithBars_(self._waves)

    def _build_waves(self, cv):
        """创建波形条。"""
        n = 28
        bw, sp = 4, 3
        tw = n * (bw + sp) - sp
        sx = (self.W - tw) / 2
        accent_cg = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.2, 0.6, 0.7, 1.0
        ).CGColor()
        for i in range(n):
            r = Foundation.NSMakeRect(sx + i * (bw + sp), 82, bw, 4)
            bar = AppKit.NSView.alloc().initWithFrame_(r)
            bar.setWantsLayer_(True)
            bar.layer().setCornerRadius_(2)
            bar.layer().setBackgroundColor_(accent_cg)
            bar.setAlphaValue_(0.5)
            cv.addSubview_(bar)
            self._waves.append(bar)

    def _label(self, text: str, x, y, w, h, size=14, bold=False, color=None):
        """创建标签。"""
        lbl = AppKit.NSTextField.alloc().initWithFrame_(
            Foundation.NSMakeRect(x, y, w, h)
        )
        lbl.setStringValue_(text)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        lbl.setTextColor_(color or TEXT)
        lbl.setFont_(AppKit.NSFont.systemFontOfSize_weight_(
            size, AppKit.NSFontWeightBold if bold else AppKit.NSFontWeightRegular
        ))
        return lbl

    # ── 公开 API ──────────────────────────────────────────────

    def show_listening(self, text: str = ""):
        """显示聆听状态。"""
        self._build()
        if not self._window:
            return
        self._text.setStringValue_("🎤 聆听中...")
        self._subtext.setStringValue_(text or "")
        self._resize(self.H_LISTEN)
        self._window.setIgnoresMouseEvents_(True)
        self._window.orderFront_(None)
        if self._delegate:
            self._delegate.startAnimation()

    def show_thinking(self):
        """显示思考状态。"""
        self._build()
        if not self._window:
            return
        if self._delegate:
            self._delegate.stopAnimation()
        self._text.setStringValue_("⏳ 思考中...")
        self._subtext.setStringValue_("")
        self._resize(self.H_LISTEN)

    def show_response(self, text: str):
        """显示回复。"""
        self._build()
        if not self._window:
            return
        if self._delegate:
            self._delegate.stopAnimation()
        display = text[:200].replace("\n", "  ")
        self._text.setStringValue_("💬 Aria")
        self._subtext.setStringValue_(display)
        h = min(self.H_RESPONSE, 100 + len(display) // 2)
        self._resize(h)
        # 自动关闭
        delay = max(3, min(8, 3 + len(display) * 0.015))
        threading.Thread(target=self._delayed_hide, args=(delay,), daemon=True).start()

    def hide(self):
        """隐藏窗口。"""
        self._build()
        if self._delegate:
            self._delegate.stopAnimation()
        if self._window:
            self._window.orderOut_(None)

    def _delayed_hide(self, delay: float):
        time.sleep(delay)
        # 需要在主线程执行
        self._window.performSelectorOnMainThread_withObject_waitUntilDone_(
            self._window.orderOut_, None, False
        )

    def _resize(self, h: float):
        if not self._window:
            return
        f = self._window.frame()
        self._window.setFrame_display_animate_(
            Foundation.NSMakeRect(f.origin.x, f.origin.y + f.size.height - h, self.W, h),
            True, True,
        )


# ── 全局单例 ────────────────────────────────────────────────────

_overlay: Optional[OverlayWindow] = None

def get() -> OverlayWindow:
    global _overlay
    if _overlay is None:
        _overlay = OverlayWindow()
    return _overlay

def show(mode: str = "listening", text: str = ""):
    o = get()
    if mode == "listening":
        o.show_listening(text)
    elif mode == "thinking":
        o.show_thinking()
    elif mode == "response":
        o.show_response(text)
    elif mode == "hide":
        o.hide()
