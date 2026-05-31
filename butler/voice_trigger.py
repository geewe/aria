"""Voice Activity & Wake Word Trigger — 语音触发检测引擎.

提供三种模式 (按优先级):
  1. Porcupine 唤醒词 (离线, 低延迟, 需 Picovoice Access Key)
  2. VAD 能量检测 (语音活动触发, 零配置)
  3. 浏览器音频流式唤醒 (客户端通过 WebSocket 发送音频)

当组件检测到唤醒词/语音活动时, 通过回调通知上层。
"""

from __future__ import annotations

import asyncio
import logging
import os
import struct
import time
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger("butler.voice_trigger")


# ── Porcupine 包装 ──────────────────────────────────────────────

class PorcupineEngine:
    """Porcupine 唤醒词引擎包装。

    使用 Picovoice Porcupine 进行离线唤醒词检测。
    需要免费 Access Key: https://console.picovoice.ai/
    """

    BUILTIN_KEYWORDS = [
        "computer", "jarvis", "alexa", "terminator",
        "hey google", "hey siri", "picovoice", "bumblebee",
        "grasshopper", "americano", "blueberry",
    ]

    def __init__(self, keyword: str = "computer", access_key: str = ""):
        self._keyword = keyword
        self._access_key = access_key or os.environ.get("PORCUPINE_ACCESS_KEY", "")
        self._porcupine = None
        self._sample_rate = 16000
        self._frame_length = 512

    def init(self) -> None:
        """初始化 Porcupine 引擎。"""
        if self._porcupine is not None:
            return
        if not self._access_key:
            raise ValueError(
                "Porcupine 需要 Access Key。\n"
                "请免费获取: https://console.picovoice.ai/\n"
                "然后设置环境变量: export PORCUPINE_ACCESS_KEY='你的密钥'"
            )
        import pvporcupine

        if self._keyword in pvporcupine.KEYWORDS:
            self._porcupine = pvporcupine.create(
                access_key=self._access_key,
                keywords=[self._keyword],
            )
        elif os.path.exists(self._keyword):
            self._porcupine = pvporcupine.create(
                access_key=self._access_key,
                keyword_paths=[self._keyword],
            )
        else:
            # 尝试当作关键词路径
            try:
                self._porcupine = pvporcupine.create(
                    access_key=self._access_key,
                    keywords=[self._keyword],
                )
            except Exception:
                raise ValueError(f"未知唤醒词: {self._keyword}. 可用: {list(pvporcupine.KEYWORDS)[:10]}")

        self._sample_rate = self._porcupine.sample_rate
        self._frame_length = self._porcupine.frame_length
        logger.info(
            f"Porcupine 唤醒词引擎已初始化: '{self._keyword}' "
            f"(frame={self._frame_length}, rate={self._sample_rate})"
        )

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def frame_length(self) -> int:
        return self._frame_length

    @property
    def is_ready(self) -> bool:
        return self._porcupine is not None

    def process(self, pcm_frame: bytes) -> bool:
        """处理一帧 PCM16 音频, 返回是否检测到唤醒词。"""
        if not self._porcupine:
            raise RuntimeError("Porcupine 未初始化")
        result = self._porcupine.process(pcm_frame)
        return result >= 0

    def close(self) -> None:
        if self._porcupine:
            self._porcupine.delete()
            self._porcupine = None

    @staticmethod
    def is_available() -> bool:
        try:
            import pvporcupine
            return True
        except ImportError:
            return False

    @staticmethod
    def available_keywords() -> list:
        try:
            import pvporcupine
            return sorted(pvporcupine.KEYWORDS)
        except ImportError:
            return []


# ── VAD 能量触发器 ──────────────────────────────────────────────

class VADTrigger:
    """能量阈值 VAD — 检测语音活动 (非唤醒词)。

    通过 RMS 能量检测用户开始说话, 作为 wake-word-less 备选方案。
    """

    def __init__(
        self,
        energy_threshold: float = 0.06,
        min_speech_frames: int = 6,   # ~180ms @ 30ms
        cooldown_sec: float = 3.0,
        sample_rate: int = 16000,
        frame_ms: int = 30,
    ):
        self.energy_threshold = energy_threshold
        self.min_speech_frames = min_speech_frames
        self.cooldown_sec = cooldown_sec
        self.sample_rate = sample_rate
        self.frame_size = int(sample_rate * frame_ms / 1000)

        self._last_trigger = 0.0
        self._speech_frames = 0
        self._is_speaking = False

    def reset(self) -> None:
        self._speech_frames = 0
        self._is_speaking = False

    @property
    def frame_length(self) -> int:
        return self.frame_size

    def feed(self, pcm_bytes: bytes) -> bool:
        """处理一帧 PCM16 音频, 返回是否检测到语音活动 (考虑冷却)。"""
        if len(pcm_bytes) < 2:
            return False

        try:
            samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            rms = np.sqrt(np.mean(samples ** 2))
        except Exception:
            rms = 0.0

        now = time.time()

        if rms > self.energy_threshold:
            self._speech_frames += 1
            if not self._is_speaking and self._speech_frames >= self.min_speech_frames:
                self._is_speaking = True
                if now - self._last_trigger > self.cooldown_sec:
                    self._last_trigger = now
                    logger.info(f"VAD trigger! (energy={rms:.4f})")
                    return True
        else:
            self._speech_frames = 0
            self._is_speaking = False

        return False

    def calibrate(self, silence_threshold: float = 0.01) -> float:
        """自动校准能量阈值 (根据环境噪音)。"""
        import sounddevice as sd
        logger.info("校准 VAD 阈值中 (采集 2 秒环境音)...")
        duration = 2.0
        samples = sd.rec(
            int(duration * self.sample_rate),
            samplerate=self.sample_rate,
            channels=1, dtype="float32",
        )
        sd.wait()
        rms = np.sqrt(np.mean(samples ** 2))
        calibrated = max(rms * 3, silence_threshold)
        logger.info(f"环境噪音 RMS={rms:.6f}, 推荐阈值={calibrated:.4f}")
        self.energy_threshold = calibrated
        return calibrated


# ── 统一唤醒引擎 ────────────────────────────────────────────────

class VoiceTrigger:
    """统一语音触发引擎。

    支持三种检测模式:
    1. Porcupine 唤醒词 (需 Access Key)
    2. VAD 能量检测 (零配置, 检测到任何语音都触发)
    3. 外部音频帧注入 (浏览器 WebSocket 流)

    用法:
        trigger = VoiceTrigger()
        trigger.on_trigger(callback)
        trigger.set_mode("porcupine", keyword="computer")
        await trigger.start_server_mic()
        # 或:
        trigger.feed(audio_frame)  # 从 WebSocket 接收浏览器音频
    """

    MODE_PORCUPINE = "porcupine"   # Porcupine 唤醒词 (需 key)
    MODE_VAD = "vad"               # 能量 VAD (零配置)
    MODE_AUTO = "auto"             # 自动选择最佳模式

    def __init__(self):
        self._mode = self.MODE_AUTO
        self._callback: Optional[Callable] = None
        self._running = False
        self._server_mic_task: Optional[asyncio.Task] = None

        # 组件
        self._porcupine: Optional[PorcupineEngine] = None
        self._vad: Optional[VADTrigger] = None

        # 外部帧缓冲 (来自 WebSocket)
        self._external_frames = asyncio.Queue(maxsize=500)
        self._external_task: Optional[asyncio.Task] = None
        self._external_active = False

        # 状态
        self._last_wake_time = 0.0
        self._wake_cooldown = 2.0  # 冷却 2 秒防重复触发

    def on_trigger(self, callback: Callable):
        """注册唤醒回调。"""
        self._callback = callback

    def set_mode(
        self,
        mode: str = "auto",
        keyword: str = "computer",
        access_key: str = "",
        energy_threshold: float = 0.06,
    ) -> str:
        """设置检测模式。返回实际启用的模式。"""
        self._mode = mode

        if mode == self.MODE_PORCUPINE or (
            mode == self.MODE_AUTO and PorcupineEngine.is_available()
        ):
            access_key = access_key or os.environ.get("PORCUPINE_ACCESS_KEY", "")
            if access_key:
                self._porcupine = PorcupineEngine(keyword, access_key)
                try:
                    self._porcupine.init()
                    self._mode = self.MODE_PORCUPINE
                    logger.info(f"✅ 启用 Porcupine 唤醒词: '{keyword}'")
                    return self._mode
                except Exception as e:
                    logger.warning(f"Porcupine 初始化失败: {e}, 回退到 VAD")

        # 回退到 VAD
        self._vad = VADTrigger(energy_threshold=energy_threshold)
        self._mode = self.MODE_VAD
        logger.info(f"✅ 启用 VAD 语音触发 (阈值={energy_threshold})")
        return self._mode

    # ── 服务器端麦克风 ──────────────────────────────────────────

    async def start_server_mic(self, device_index: int = -1):
        """启动服务器端麦克风监听 (使用 sounddevice)。"""
        if self._running:
            return
        self._running = True
        self._server_mic_task = asyncio.create_task(
            self._run_server_mic(device_index)
        )
        logger.info("服务器端麦克风唤醒监听已启动")

    async def _run_server_mic(self, device_index: int):
        """在 executor 中运行 sounddevice 监听循环。"""
        import sounddevice as sd

        if self._mode == self.MODE_PORCUPINE and self._porcupine:
            frame_len = self._porcupine.frame_length
            sample_rate = self._porcupine.sample_rate
            process_fn = self._porcupine.process
        elif self._vad:
            frame_len = self._vad.frame_length
            sample_rate = self._vad.sample_rate
            process_fn = lambda f: self._vad.feed(f)
        else:
            logger.error("没有可用的检测引擎")
            self._running = False
            return

        def audio_callback(indata, frames, time_info, status):
            if not self._running:
                raise sd.CallbackStop()
            if status:
                logger.debug(f"sounddevice status: {status}")

            pcm = (indata[:, 0] * 32767).astype(np.int16).tobytes()
            detected = process_fn(pcm)
            if detected and self._callback:
                now = time.time()
                if now - self._last_wake_time > self._wake_cooldown:
                    self._last_wake_time = now
                    logger.info("🎤 服务器麦克风检测到触发")
                    self._callback()

        try:
            with sd.InputStream(
                device=device_index if device_index >= 0 else None,
                samplerate=sample_rate,
                blocksize=frame_len,
                channels=1,
                dtype="float32",
                callback=audio_callback,
            ):
                while self._running:
                    await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"服务器麦克风错误: {e}")
        finally:
            self._running = False

    def stop_server_mic(self):
        """停止服务器端麦克风。"""
        self._running = False
        if self._server_mic_task:
            self._server_mic_task.cancel()
            self._server_mic_task = None

    # ── 外部音频帧注入 (浏览器 WebSocket 流) ──────────────────

    async def start_external(self):
        """启动外部音频帧处理任务 (处理来自 WebSocket 的帧)。"""
        if self._external_active:
            return
        self._external_active = True
        self._external_task = asyncio.create_task(self._process_external_frames())
        logger.info("外部音频帧处理已启动")

    def feed_external(self, pcm_frame: bytes):
        """从外部 (如浏览器 WebSocket) 注入一帧 PCM16 音频。"""
        if not self._external_active:
            return
        try:
            self._external_frames.put_nowait(pcm_frame)
        except asyncio.QueueFull:
            pass  # 丢弃溢出帧

    async def _process_external_frames(self):
        """处理外部音频帧队列。"""
        if self._mode == self.MODE_PORCUPINE and self._porcupine and self._porcupine.is_ready:
            porcupine = self._porcupine
            process_fn = lambda f: porcupine.process(f)
            required_len = porcupine.frame_length * 2  # 16-bit = 2 bytes per sample
        elif self._vad:
            process_fn = lambda f: self._vad.feed(f)
            required_len = self._vad.frame_length * 2
        else:
            logger.error("没有可用的外部帧检测引擎")
            return

        buffer = bytearray()

        while self._external_active:
            try:
                frame = await asyncio.wait_for(
                    self._external_frames.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            buffer.extend(frame)

            # 累积到足够长度再处理
            while len(buffer) >= required_len:
                chunk = bytes(buffer[:required_len])
                buffer = buffer[required_len:]

                detected = process_fn(chunk)
                if detected and self._callback:
                    now = time.time()
                    if now - self._last_wake_time > self._wake_cooldown:
                        self._last_wake_time = now
                        logger.info("🌐 外部音频检测到唤醒")
                        self._callback()
                        buffer.clear()

        self._external_active = False

    async def stop_external(self):
        """停止外部音频帧处理。"""
        self._external_active = False
        if self._external_task:
            self._external_task.cancel()
            self._external_task = None

    # ── 生命周期 ────────────────────────────────────────────────

    def stop(self):
        """停止所有监听。"""
        self.stop_server_mic()
        if self._external_active:
            # 不能直接 await, 安排取消
            self._external_active = False

    def close(self):
        """释放所有资源。"""
        self.stop()
        if self._porcupine:
            self._porcupine.close()
            self._porcupine = None

    @property
    def is_active(self) -> bool:
        return self._running or self._external_active

    @property
    def current_mode(self) -> str:
        return self._mode

    @property
    def mode_display(self) -> str:
        if self._mode == self.MODE_PORCUPINE and self._porcupine:
            return f"Porcupine({self._porcupine._keyword})"
        elif self._mode == self.MODE_VAD and self._vad:
            return f"VAD(阈值={self._vad.energy_threshold:.4f})"
        return "none"


# ── 工厂函数 ────────────────────────────────────────────────────

def create_voice_trigger(
    on_wake: Callable,
    mode: str = "auto",
    keyword: str = "computer",
    access_key: str = "",
    energy_threshold: float = 0.06,
) -> VoiceTrigger:
    """创建并配置语音触发器。"""
    trigger = VoiceTrigger()
    trigger.set_mode(mode, keyword, access_key, energy_threshold)
    trigger.on_trigger(on_wake)
    return trigger
