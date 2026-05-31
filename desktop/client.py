"""Aria 桌面客户端 — 菜单栏 + 浮动覆盖层 + 唤醒词。

类 Siri 体验:
  - 菜单栏常驻图标
  - 说 "Hey Aria" (或配置的关键词) 唤醒
  - 浮动覆盖窗口显示对话
  - TTS 语音回复
  - 全局快捷键

使用:
    python3 desktop/client.py --server wss://127.0.0.1:8653
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import ssl
import sys
import threading
import time
from typing import Optional

import rumps

# 确保可以找到 butler 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from butler.voice_trigger import VoiceTrigger, PorcupineEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("aria.desktop")

# ── 配置 ────────────────────────────────────────────────────────

DEFAULT_SERVER = "wss://127.0.0.1:8653"
APP_NAME = "Aria"
ICON_BASE = "🔊"  # 使用 emoji 作为临时图标


# ── 桌面客户端 ──────────────────────────────────────────────────

class AriaDesktopApp(rumps.App):
    """Aria 桌面菜单栏应用。"""

    def __init__(self, server_url: str, wake_keyword: str = "computer"):
        super().__init__(APP_NAME, icon=None, quit_button=None)
        self.server_url = server_url
        self.wake_keyword = wake_keyword

        # 状态
        self._ws = None
        self._ws_connected = False
        self._is_listening = False
        self._is_processing = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws_thread: Optional[threading.Thread] = None

        # 唤醒词
        self._trigger: Optional[VoiceTrigger] = None
        self._wake_active = False

        # 菜单
        self.menu = [
            rumps.MenuItem("🎤 切换唤醒", callback=self._toggle_wake),
            rumps.MenuItem("🔊 手动对话", callback=self._manual_trigger),
            None,  # 分隔线
            rumps.MenuItem("● 已断开", callback=None),
            rumps.MenuItem("⚙ 设置", callback=self._show_settings),
            None,
            rumps.MenuItem("退出", callback=self._quit),
        ]
        self._status_item = self.menu["● 已断开"]

        # 启动后台线程
        self._start_background()

    def _start_background(self):
        """启动 asyncio 事件循环线程。"""
        self._loop = asyncio.new_event_loop()
        self._ws_thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="aria-asyncio",
        )
        self._ws_thread.start()
        # 安排 WebSocket 连接
        asyncio.run_coroutine_threadsafe(self._connect_ws(), self._loop)

    def _run_loop(self):
        """运行 asyncio 事件循环。"""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    # ── WebSocket ──────────────────────────────────────────────

    async def _connect_ws(self):
        """连接到 WebSocket 服务器。"""
        import websockets

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        while True:
            try:
                logger.info(f"Connecting to {self.server_url}...")
                async with websockets.connect(self.server_url, ssl=ssl_ctx) as ws:
                    self._ws = ws
                    self._ws_connected = True
                    self._update_status("🟢 已连接")
                    logger.info("WebSocket connected")

                    # 接收 connected 消息
                    msg = await ws.recv()
                    data = json.loads(msg)
                    logger.info(f"Server: {data.get('version', '?')}")

                    # 主消息循环
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            await self._handle_message(data)
                        except json.JSONDecodeError:
                            continue

            except Exception as e:
                self._ws_connected = False
                self._update_status("🔴 已断开")
                logger.warning(f"WS disconnected: {e}")
                self._ws = None
                await asyncio.sleep(5)

    async def _handle_message(self, data: dict):
        """处理服务器消息。"""
        msg_type = data.get("type", "")
        logger.debug(f"Server msg: {msg_type}")

        if msg_type == "connected":
            self.title = f"{APP_NAME} v{data.get('version', '?')}"

        elif msg_type == "wake":
            # 服务器检测到唤醒词
            logger.info("🎤 Wake event received!")
            if not self._is_listening:
                self._start_listening()

        elif msg_type == "state_change":
            state = data.get("to", "")
            if state == "listening":
                pass  # 已在监听
            elif state == "processing":
                await self._show_thinking()

        elif msg_type == "llm_start":
            rumps.notification(
                APP_NAME, "Aria", data.get("text", ""),
                sound=False,
            )

        elif msg_type == "llm_end":
            text = data.get("text", "")
            await self._show_response(text)

        elif msg_type == "tts_start":
            pass  # TTS 正在播放

    def _send_json(self, data: dict):
        """发送 JSON 消息到服务器。"""
        if not self._ws or not self._ws_connected:
            logger.warning("Cannot send: not connected")
            return
        asyncio.run_coroutine_threadsafe(
            self._ws.send(json.dumps(data)),
            self._loop,
        )

    def _send_audio(self, frame: bytes):
        """发送音频帧到服务器。"""
        if not self._ws or not self._ws_connected:
            return
        asyncio.run_coroutine_threadsafe(
            self._ws.send(frame),
            self._loop,
        )

    # ── 监听 ──────────────────────────────────────────────────

    def _start_listening(self):
        """开始语音监听。"""
        if self._is_listening:
            return
        self._is_listening = True
        self._send_json({"type": "wake_audio_start"})
        self._show_overlay("listening")
        # 启动本地麦克风采集
        threading.Thread(target=self._capture_audio, daemon=True).start()

    def _stop_listening(self):
        """停止语音监听。"""
        if not self._is_listening:
            return
        self._is_listening = False
        self._send_json({"type": "wake_audio_stop"})

    def _capture_audio(self):
        """采集麦克风音频并发送到服务器。"""
        import sounddevice as sd
        import numpy as np

        frame_length = 512  # Porcupine 帧大小
        sample_rate = 16000

        def callback(indata, frames, time_info, status):
            if not self._is_listening:
                raise sd.CallbackStop()
            if status:
                logger.debug(f"Audio status: {status}")
            # 转换为 PCM16
            pcm = (indata[:, 0] * 32767).astype(np.int16).tobytes()
            self._send_audio(pcm)

        try:
            with sd.InputStream(
                samplerate=sample_rate,
                blocksize=frame_length,
                channels=1,
                dtype="float32",
                callback=callback,
            ):
                while self._is_listening:
                    time.sleep(0.1)
        except Exception as e:
            logger.error(f"Audio capture error: {e}")
            self._is_listening = False

    # ── 唤醒词 ────────────────────────────────────────────────

    def _init_wake_word(self):
        """初始化唤醒词引擎。"""
        if self._trigger:
            return

        self._trigger = VoiceTrigger()

        # 尝试 Porcupine
        access_key = os.environ.get("PORCUPINE_ACCESS_KEY", "")
        if access_key:
            mode = self._trigger.set_mode(
                "porcupine",
                keyword=self.wake_keyword,
                access_key=access_key,
            )
            logger.info(f"Wake word mode: {mode}")
        else:
            # 使用 VAD 模式
            self._trigger.set_mode("vad")
            logger.info("Wake word: VAD mode (no Porcupine key)")

        self._trigger.on_trigger(self._on_wake_detected)

    def _on_wake_detected(self):
        """唤醒词检测到回调。"""
        logger.info("🚀 Wake word detected!")
        # 在主线程中处理
        rumps.notification(APP_NAME, "🎤 已唤醒", "请说出您的指令")
        self._start_listening()

    def _toggle_wake(self, sender):
        """切换唤醒开关。"""
        if self._wake_active:
            self._stop_wake()
            sender.title = "🎤 切换唤醒 (关闭)"
        else:
            self._start_wake()
            sender.title = "🎤 切换唤醒 (开启)"

    def _start_wake(self):
        """启动唤醒词监听。"""
        if self._wake_active:
            return
        self._init_wake_word()
        self._wake_active = True

        # 在后台线程中运行服务器端麦克风
        asyncio.run_coroutine_threadsafe(
            self._trigger.start_server_mic(),
            self._loop,
        )

        logger.info("Wake word listening started")
        rumps.notification(
            APP_NAME, "🎤 唤醒已开启",
            f"说 '{self.wake_keyword}' 唤醒 Aria",
        )

    def _stop_wake(self):
        """停止唤醒词监听。"""
        if not self._wake_active:
            return
        self._wake_active = False
        if self._trigger:
            self._trigger.stop_server_mic()
        logger.info("Wake word listening stopped")

    # ── 手动触发 ──────────────────────────────────────────────

    def _manual_trigger(self, sender):
        """手动触发对话。"""
        logger.info("Manual trigger")
        self._start_listening()

    # ── 覆盖层 ────────────────────────────────────────────────

    def _show_overlay(self, mode: str, text: str = ""):
        """在 AppKit 主线程中显示覆盖层。"""
        from .overlay import show_overlay
        # pyobjc 需要在主线程操作 UI
        Foundation = __import__("Foundation")
        Foundation.NSRunLoop.mainRunLoop().performBlock_(
            lambda: show_overlay(mode, text)
        )

    async def _show_thinking(self):
        """显示思考中状态。"""
        self._is_listening = False
        self._show_overlay("thinking")

    async def _show_response(self, text: str):
        """显示回复。"""
        self._is_processing = False
        self._show_overlay("response", text)
        # 3 秒后回到待命
        await asyncio.sleep(3)
        if self._wake_active:
            self._stop_listening()
            self._start_wake()

    # ── UI ─────────────────────────────────────────────────────

    def _update_status(self, text: str):
        """更新菜单栏状态显示。"""
        try:
            self._status_item.title = text
        except Exception:
            pass

    def _show_settings(self, sender):
        """显示设置 (后续实现)。"""
        rumps.alert(
            title="Aria 设置",
            message=f"服务器: {self.server_url}\n"
                    f"唤醒词: {self.wake_keyword}\n"
                    f"状态: {'已连接' if self._ws_connected else '已断开'}\n"
                    f"唤醒: {'开启' if self._wake_active else '关闭'}\n\n"
                    f"设置环境变量 PORCUPINE_ACCESS_KEY 启用 Porcupine 唤醒词",
        )

    def _quit(self, sender):
        """退出应用。"""
        logger.info("Shutting down...")
        self._stop_wake()
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        rumps.quit_application()


# ── 入口 ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Aria Desktop Client")
    parser.add_argument(
        "--server", default=os.environ.get("ARIA_SERVER", DEFAULT_SERVER),
        help=f"WebSocket 服务器地址 (默认: {DEFAULT_SERVER})",
    )
    parser.add_argument(
        "--keyword", default=os.environ.get("ARIA_WAKE_KEYWORD", "computer"),
        help="唤醒关键词 (默认: computer)",
    )
    args = parser.parse_args()

    # 启动 AppKit 应用 (必须在主线程)
    app = AriaDesktopApp(server_url=args.server, wake_keyword=args.keyword)

    logger.info(f"Aria Desktop Client started")
    logger.info(f"  Server: {args.server}")
    logger.info(f"  Wake keyword: {args.keyword}")

    app.run()


if __name__ == "__main__":
    main()
