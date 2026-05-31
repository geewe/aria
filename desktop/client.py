"""Aria 桌面客户端 — 菜单栏常驻 + 类 Siri 唤醒。

必须在主线程运行 (AppKit 要求)。
asyncio 事件循环在后台线程运行。
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
from butler.voice_trigger import VoiceTrigger

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

        # 状态
        self._ws = None
        self._ws_connected = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None

        # 唤醒词
        self._trigger: Optional[VoiceTrigger] = None
        self._wake_active = False

        # 音频监听
        self._is_listening = False

        # 菜单
        self._status_item = rumps.MenuItem("● 启动中...", callback=None)
        self.menu = [
            self._status_item,
            rumps.MenuItem("🎤 语音唤醒", callback=self._toggle_wake),
            rumps.MenuItem("🔊 手动对话", callback=self._manual_trigger),
            None,
            rumps.MenuItem("⚙ 设置", callback=self._show_settings),
            None,
            rumps.MenuItem("退出", callback=self._quit),
        ]

        # 启动后台 asyncio
        self._start_async_loop()

    def _start_async_loop(self):
        """在后台线程启动 asyncio 事件循环。"""
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_async, daemon=True, name="aria-asyncio"
        )
        self._loop_thread.start()
        # 安排 WebSocket 连接
        asyncio.run_coroutine_threadsafe(self._connect_ws(), self._loop)

    def _run_async(self):
        """后台 asyncio 事件循环。"""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _call_async(self, coro):
        """在 asyncio 事件循环中执行协程。"""
        if self._loop and self._loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ── WebSocket ──────────────────────────────────────────────

    async def _connect_ws(self):
        """连接到 WebSocket 服务器 (自动重连)。"""
        import websockets

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        ssl_ctx.load_default_certs()

        while True:
            try:
                logger.info(f"Connecting to {self.server_url}...")
                async with websockets.connect(self.server_url, ssl=ssl_ctx) as ws:
                    self._ws = ws
                    self._ws_connected = True
                    self._update_status("🟢 已连接")
                    logger.info("WebSocket connected")

                    msg = await ws.recv()
                    data = json.loads(msg)
                    logger.info(f"Server: {data.get('version', '?')}")

                    async for message in ws:
                        try:
                            data = json.loads(message)
                            await self._handle_message(data)
                        except json.JSONDecodeError:
                            continue
                        except Exception as e:
                            logger.error(f"Msg handler error: {e}")

            except Exception as e:
                self._ws_connected = False
                self._ws = None
                self._update_status("🔴 已断开")
                logger.warning(f"WS error: {e}, retry in 5s...")
                await asyncio.sleep(5)

    async def _handle_message(self, data: dict):
        """处理服务器消息。"""
        msg_type = data.get("type", "")

        if msg_type == "wake":
            logger.info("🎤 Wake event from server!")
            self._start_listening()

        elif msg_type == "llm_start":
            text = data.get("text", "")
            rumps.notification("Aria", "🎤 已收到", text or "正在处理...")

        elif msg_type == "llm_end":
            text = data.get("text", "")
            self._show_response(text)

    def _send(self, data: dict):
        """发送 JSON 消息到服务器。"""
        if not self._ws or not self._ws_connected:
            return
        self._call_async(self._ws.send(json.dumps(data)))

    def _send_audio(self, frame: bytes):
        """发送音频帧到服务器。"""
        if not self._ws or not self._ws_connected:
            return
        self._call_async(self._ws.send(frame))

    # ── 唤醒词 ────────────────────────────────────────────────

    def _toggle_wake(self, sender):
        """切换唤醒开关。"""
        if self._wake_active:
            self._stop_wake()
            sender.title = "🎤 开启唤醒"
            self._update_status("🔊 唤醒关闭")
        else:
            self._start_wake()
            sender.title = "🎤 关闭唤醒"
            self._update_status("🎤 唤醒中...")

    def _start_wake(self):
        """启动服务器端唤醒词监听。"""
        if self._wake_active:
            return
        self._wake_active = True

        self._trigger = VoiceTrigger()
        access_key = os.environ.get("PORCUPINE_ACCESS_KEY", "")
        if access_key:
            self._trigger.set_mode("porcupine", keyword=self.wake_keyword, access_key=access_key)
        else:
            # 智能 VAD 模式: FFT 频谱分析 + 自动校准
            # 只能检测人声 (非噪声), 首次启动自动采集环境音校准
            self._trigger.set_mode("vad")
        self._trigger.on_trigger(self._on_wake_detected)

        self._call_async(self._trigger.start_server_mic())
        logger.info(f"Wake word started: {self._trigger.mode_display}")

    def _stop_wake(self):
        """停止唤醒词监听。"""
        self._wake_active = False
        if self._trigger:
            self._trigger.stop_server_mic()
        logger.info("Wake word stopped")

    def _on_wake_detected(self):
        """唤醒检测 - 忽略重复触发"""
        if self._is_listening or not self._wake_active:
            return
        logger.info("🚀 Wake word detected!")
        self._start_listening()

    def _manual_trigger(self, sender):
        """手动触发对话。"""
        rumps.notification("Aria", "🎤 手动触发", "聆听中...")
        self._start_listening()

    def _start_listening(self):
        """VAD触发后: 停止唤醒监听, 开始对话录音"""
        if self._is_listening:
            return
        self._is_listening = True
        self._send({"type": "wake_audio_stop"})
        self._update_status("🎤 聆听中")
        self._show_overlay("listening")
        self._speech_started = True
        self._send({"type": "vad", "state": "speech_start"})
        threading.Thread(target=self._capture_audio, daemon=True).start()

    def _capture_audio(self):
        """采集麦克风并发送到对话管线 (后台线程)"""
        import sounddevice as sd
        import numpy as np

        frame_len = 480
        sample_rate = 16000

        def callback(indata, frames, time_info, status):
            if not self._is_listening:
                raise sd.CallbackStop()
            pcm = (indata[:, 0] * 32767).astype(np.int16).tobytes()
            self._send_audio(pcm)

        try:
            with sd.InputStream(
                samplerate=sample_rate, blocksize=frame_len,
                channels=1, dtype="float32", callback=callback,
            ):
                for _ in range(150):
                    time.sleep(0.1)
                    if not self._is_listening:
                        break
        except Exception as e:
            logger.error(f"音频采集错误: {e}")
        finally:
            if self._speech_started:
                self._send({"type": "vad", "state": "speech_end"})
                self._speech_started = False
            self._is_listening = False

    def _stop_listening(self):
        """停止对话录音"""
        if not self._is_listening:
            return
        self._is_listening = False
        self._update_status("Aria")
        self._show_overlay("hide")

    def _show_response(self, text: str):
        """显示回复并自动关闭, 之后重新进入唤醒模式"""
        self._is_listening = False
        self._show_overlay("response", text)
        self._update_status("Aria")
        def delayed_restart():
            time.sleep(2)
            if self._wake_active:
                self._start_wake()
        threading.Thread(target=delayed_restart, daemon=True).start()
    def _update_status(self, text: str):
        try:
            self._status_item.title = text
        except Exception:
            pass

    def _show_settings(self, sender):
        rumps.alert(
            title="Aria 设置",
            message=f"服务器: {self.server_url}\n"
                    f"唤醒词: {self.wake_keyword}\n"
                    f"状态: {'🟢 已连接' if self._ws_connected else '🔴 已断开'}\n"
                    f"唤醒: {'🟢 开启' if self._wake_active else '🔴 关闭'}\n\n"
                    f"设置 PORCUPINE_ACCESS_KEY 环境变量启用 Porcupine",
        )

    def _quit(self, sender):
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
    )
    parser.add_argument(
        "--keyword", default=os.environ.get("ARIA_WAKE_KEYWORD", "computer"),
    )
    args = parser.parse_args()

    logger.info("Aria Desktop Client started")
    logger.info(f"  Server: {args.server}")
    logger.info(f"  Wake keyword: {args.keyword}")

    app = AriaDesktopApp(server_url=args.server, wake_keyword=args.keyword)
    app.run()  # 阻塞 — 在主线程运行 AppKit 事件循环


if __name__ == "__main__":
    main()
