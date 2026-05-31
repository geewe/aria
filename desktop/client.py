"""Aria 桌面客户端 — 菜单栏常驻 + 类 Siri 唤醒。

架构: 单音频流设计 (避免双 InputStream 冲突)
  唤醒模式 → 音频帧发到 voice_trigger 做 VAD 检测
  对话模式 → 音频帧发到 orchestrator 做 STT 识别

必须在主线程运行 (AppKit 要求), asyncio 在后台线程。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import ssl
import sys
import threading
import time
from typing import Optional

import rumps

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("aria.desktop")

DEFAULT_SERVER = "wss://127.0.0.1:8653/ws"


class AriaDesktopApp(rumps.App):
    """Aria 菜单栏应用 — 主线程运行 (rumps/AppKit 要求)。"""

    def __init__(self, server_url: str, wake_keyword: str):
        super().__init__("🔊 Aria", quit_button=None)
        self.server_url = server_url
        self.wake_keyword = wake_keyword

        # WebSocket
        self._ws = None
        self._ws_connected = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # 音频状态
        self._capture_running = False        # 音频采集线程是否运行
        self._mode_wake = False              # 唤醒模式开关 (菜单栏)
        self._mode_conversation = False      # 对话模式中 (VAD触发后)
        
        # 菜单
        self._status_item = rumps.MenuItem("● 启动中...", callback=None)
        self.menu = [
            self._status_item,
            rumps.MenuItem("🎤 语音唤醒", callback=self._toggle_wake),
            None,
            rumps.MenuItem("⚙ 设置", callback=self._show_settings),
            None,
            rumps.MenuItem("退出", callback=self._quit),
        ]

        # 启动后台
        self._start_async_loop()

    def _start_async_loop(self):
        self._loop = asyncio.new_event_loop()
        t = threading.Thread(target=self._run_async, daemon=True, name="aria-loop")
        t.start()
        asyncio.run_coroutine_threadsafe(self._connect_ws(), self._loop)

    def _run_async(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _call_async(self, coro):
        if self._loop and self._loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ── WebSocket ──────────────────────────────────────────────

    async def _connect_ws(self):
        import websockets
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        while True:
            try:
                async with websockets.connect(self.server_url, ssl=ssl_ctx) as ws:
                    self._ws = ws
                    self._ws_connected = True
                    self._update_status("🟢 已连接")
                    msg = json.loads(await ws.recv())
                    logger.info(f"Server: {msg.get('version', '?')}")

                    async for message in ws:
                        try:
                            await self._handle_message(json.loads(message))
                        except Exception as e:
                            logger.error(f"Msg error: {e}")
            except Exception as e:
                self._ws_connected = False
                self._ws = None
                self._update_status("🔴 已断开")
                logger.warning(f"WS: {e}, retry 5s...")
                await asyncio.sleep(5)

    async def _handle_message(self, data: dict):
        t = data.get("type", "")
        
        if t == "wake":
            logger.info("🎤 Wake from server!")
            if self._mode_wake and not self._mode_conversation:
                self._enter_conversation()

        elif t == "llm_start":
            text = data.get("text", "")
            if self._mode_conversation:
                self._show_overlay("thinking")

        elif t == "llm_end":
            text = data.get("text", "")
            self._show_response(text)

        elif t == "state_change":
            state = data.get("to", "")
            if state == "listening":
                self._show_overlay("listening")
            elif state == "idle":
                pass

    def _send(self, data: dict):
        if not self._ws or not self._ws_connected:
            return
        self._call_async(self._ws.send(json.dumps(data)))

    def _send_audio(self, frame: bytes):
        if not self._ws or not self._ws_connected:
            logger.debug(f'Audio dropped: ws={self._ws is not None}, connected={self._ws_connected}')
            return
        self._call_async(self._ws.send(frame))

    # ── 音频采集 (单一流) ────────────────────────────────────

    def _start_capture(self):
        """启动单一音频采集线程。"""
        if self._capture_running:
            return
        self._capture_running = True
        threading.Thread(target=self._audio_loop, daemon=True, name="aria-audio").start()

    def _stop_capture(self):
        self._capture_running = False

    def _audio_loop(self):
        """音频采集循环 — 运行期间持续采集麦克风。

        根据模式切换发送目标:
          - 唤醒模式: 作为 wake_audio 帧 → 服务器 voice_trigger
          - 对话模式: 作为普通音频帧 → 服务器 orchestrator
        """
        import sounddevice as sd
        import numpy as np

        frame_len = 480
        sample_rate = 16000

        def callback(indata, frames, time_info, status):
            if not self._capture_running:
                raise sd.CallbackStop()
            if status:
                logger.debug(f"Audio status: {status}")

            rms = float(np.sqrt(np.mean(indata[:, 0] ** 2)))
            pcm = (indata[:, 0] * 32767).astype(np.int16).tobytes()
            self._send_audio(pcm)
            if rms > 0.005:
                logger.debug(f'Audio frame: rms={rms:.4f}, len={len(pcm)}')

        try:
            with sd.InputStream(
                samplerate=sample_rate, blocksize=frame_len,
                channels=1, dtype="float32", callback=callback,
            ):
                while self._capture_running:
                    time.sleep(0.5)
        except Exception as e:
            logger.error(f"音频采集错误: {e}")
            self._capture_running = False

    # ── 唤醒模式 ──────────────────────────────────────────────

    def _toggle_wake(self, sender):
        if self._mode_wake:
            self._disable_wake()
            sender.title = "🎤 语音唤醒"
            self._update_status("🔊 唤醒关闭")
        else:
            self._enable_wake()
            sender.title = "🎤 关闭唤醒"
            self._update_status("🎤 唤醒中...")

    def _enable_wake(self):
        """开启唤醒: 启动音频采集, 直接发送到对话管线。"""
        if self._mode_wake:
            return
        self._mode_wake = True
        self._mode_conversation = False
        self._start_capture()
        self._update_status("🎤 聆听中")
        logger.info("Wake enabled: audio streaming to orchestrator")

    def _disable_wake(self):
        """关闭唤醒。"""
        self._mode_wake = False
        self._mode_conversation = False
        self._stop_capture()
        self._update_status("🔊 唤醒关闭")
        logger.info("Wake disabled")

    # ── 对话模式 ──────────────────────────────────────────────

    def _enter_conversation(self):
        """VAD触发: 进入对话模式。"""
        if self._mode_conversation:
            return
        self._mode_conversation = True
        self._update_status("🎤 对话中")
        self._show_overlay("listening")

        # 15秒超时
        def timeout_check():
            started = time.time()
            while self._mode_conversation and time.time() - started < 15:
                time.sleep(0.5)
            if self._mode_conversation:
                logger.info("对话超时")
                self._exit_conversation()
        threading.Thread(target=timeout_check, daemon=True).start()

    def _exit_conversation(self):
        """退出对话模式。"""
        if not self._mode_conversation:
            return
        self._mode_conversation = False
        self._show_overlay("hide")
        self._update_status("🎤 聆听中")

    def _show_response(self, text: str):
        """显示回复, 然后回到聆听状态。"""
        self._mode_conversation = False
        self._show_overlay("response", text)
        self._update_status("🔊 Aria")
        def rearm():
            time.sleep(1.5)
            if self._mode_wake:
                self._update_status("🎤 聆听中")
        threading.Thread(target=rearm, daemon=True).start()

    # ── 覆盖层 ────────────────────────────────────────────────

    def _show_overlay(self, mode: str, text: str = ""):
        try:
            from desktop.overlay import show
            import Foundation
            Foundation.NSObject.performSelectorOnMainThread_withObject_waitUntilDone_(
                lambda: show(mode, text), None, False
            )
        except Exception as e:
            logger.warning(f"Overlay: {e}")

    # ── UI ─────────────────────────────────────────────────────

    def _update_status(self, text: str):
        try:
            self._status_item.title = text
        except Exception:
            pass

    def _show_settings(self, sender):
        rumps.alert(
            title="Aria 设置",
            message=f"服务器: {self.server_url}\n"
                    f"唤醒: {'🟢 开启' if self._mode_wake else '🔴 关闭'}\n"
                    f"状态: {'🟢 已连接' if self._ws_connected else '🔴 已断开'}\n"
                    f"关键词: {self.wake_keyword}\n\n"
                    f"设置 PORCUPINE_ACCESS_KEY 启用 Porcupine\n"
                    f"(默认使用 VAD 频谱分析)",
        )

    def _quit(self, sender):
        logger.info("Shutting down...")
        self._disable_wake()
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        rumps.quit_application()


def main():
    parser = argparse.ArgumentParser(description="Aria Desktop Client")
    parser.add_argument("--server", default=os.environ.get("ARIA_SERVER", DEFAULT_SERVER))
    parser.add_argument("--keyword", default=os.environ.get("ARIA_WAKE_KEYWORD", "computer"))
    args = parser.parse_args()

    logger.info("Aria Desktop Client (单音频流版)")
    logger.info(f"  Server: {args.server}")
    logger.info(f"  Wake keyword: {args.keyword}")

    app = AriaDesktopApp(server_url=args.server, wake_keyword=args.keyword)
    app.run()


if __name__ == "__main__":
    main()
