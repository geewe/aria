"""Aria 浮动覆盖层 — 类 Siri 式对话视图。

使用 AppKit (pyobjc) 创建原生 macOS 浮动窗口。
通过 Foundation.NSRunLoop 主线程操作 UI。
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


# ── 辅助: 在主线程执行 UI 操作 ──────────────────────────────────

def _main_thread(fn, *args, delay=0.0):
    """在 AppKit 主线程上同步执行函数。"""
    if threading.current_thread() is threading.main_thread():
        return fn(*args)
    result = [None, threading.Event()]

    def wrapper():
        try:
            result[0] = fn(*args)
        except Exception as e:
            result[0] = e
        finally:
            result[1].set()

    if delay > 0:
        Foundation.NSObject.performSelector_withObject_afterDelay_(
            wrapper, None, delay
        )
    else:
        Foundation.NSObject.performSelectorOnMainThread_withObject_waitUntilDone_(
            wrapper, None, True
        )
    result[1].wait()
    if isinstance(result[0], Exception):
        raise result[0]
    return result[0]


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
        self._wave_running = False
        self._wave_thread: Optional[threading.Thread] = None
        self._visible = False

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
        cv.layer().setBackgroundColor_(BG.CGColor())

        # 文字
        self._text = self._label("🎤 聆听中...", 20, 40, self.W - 40, 30, 18, bold=True)
        cv.addSubview_(self._text)

        self._subtext = self._label("", 20, 12, self.W - 40, 22, 13, color=DIM)
        self._subtext.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        cv.addSubview_(self._subtext)

        # 波形条
        self._build_waves(cv)

    def _build_waves(self, cv):
        """创建波形条。"""
        n = 28
        bw, sp = 4, 3
        tw = n * (bw + sp) - sp
        sx = (self.W - tw) / 2
        for i in range(n):
            r = Foundation.NSMakeRect(sx + i * (bw + sp), 82, bw, 4)
            bar = AppKit.NSView.alloc().initWithFrame_(r)
            bar.setWantsLayer_(True)
            bar.layer().setCornerRadius_(2)
            bar.layer().setBackgroundColor_(ACCENT.CGColor())
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

    # ── 波形动画 ──────────────────────────────────────────────

    def _wave_loop(self):
        """波形动画线程。"""
        phase = 0.0
        while self._wave_running:
            phase += 0.15
            for i, bar in enumerate(self._waves):
                h = abs(math.sin(phase + i * 0.4)) * 22 + 4
                self._set_bar(bar, h)
            time.sleep(1 / 30)

    def _set_bar(self, bar, height):
        """在主线程更新波形高度。"""
        Foundation.NSObject.performSelectorOnMainThread_withObject_waitUntilDone_(
            lambda: bar.setFrameSize_(Foundation.NSMakeSize(4, height)),
            None,
            False,
        )

    # ── 公开 API ──────────────────────────────────────────────

    def show_listening(self, text: str = ""):
        """显示聆听状态。"""
        _main_thread(self._build)
        if not self._window:
            return
        self._visible = True
        self._text.setStringValue_("🎤 聆听中...")
        self._subtext.setStringValue_(text or "")
        self._resize(self.H_LISTEN)
        self._window.setIgnoresMouseEvents_(True)
        self._window.orderFront_(None)
        self._start_wave()

    def show_thinking(self):
        """显示思考状态。"""
        _main_thread(self._build)
        if not self._window:
            return
        self._stop_wave()
        self._text.setStringValue_("⏳ 思考中...")
        self._subtext.setStringValue_("")
        self._resize(self.H_LISTEN)

    def show_response(self, text: str):
        """显示回复。"""
        _main_thread(self._build)
        if not self._window:
            return
        self._stop_wave()
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
        _main_thread(self._hide_inner)

    def _hide_inner(self):
        if self._window:
            self._stop_wave()
            self._window.orderOut_(None)
        self._visible = False

    def _delayed_hide(self, delay: float):
        time.sleep(delay)
        self.hide()

    def _start_wave(self):
        self._stop_wave()
        self._wave_running = True
        self._wave_thread = threading.Thread(target=self._wave_loop, daemon=True)
        self._wave_thread.start()

    def _stop_wave(self):
        self._wave_running = False
        for bar in self._waves:
            bar.setFrameSize_(Foundation.NSMakeSize(4, 4))
            bar.setAlphaValue_(0.5)

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
