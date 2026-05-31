"""Wake Word Detection — 唤醒词检测模块。

提供两种模式:
  1. Porcupine (离线, 低延迟, 需 Picovoice Console 训练自定义词)
  2. Web Speech API (浏览器端, 零配置, 见前端实现)

当前使用内置关键词 "computer" / "jarvis" 作为备选。
要使用自定义 "Hey Aria" 唤醒词, 需在 Picovoice Console 训练。
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import AsyncGenerator, Callable, Optional

logger = logging.getLogger("butler.wakeword")


# Porcupine 访问密钥 (免费: https://console.picovoice.ai/)
# 可以从环境变量 PORCUPINE_ACCESS_KEY 读取
DEFAULT_ACCESS_KEY = ""


class WakeWordDetector:
    """唤醒词检测器 — 基于 Porcupine 引擎。

    用法:
        detector = WakeWordDetector()
        detector.set_keyword("computer")
        async for event in detector.listen():
            if event == "wake":
                print("唤醒!")
    """

    # 内置关键词映射
    BUILTIN_KEYWORDS = {
        "computer": "电脑管家",
        "jarvis": "贾维斯",
        "alexa": "Alexa",
        "terminator": "终结者",
        "hey google": "Hey Google",
        "hey siri": "Hey Siri",
    }

    def __init__(self, keyword: str = "computer", access_key: str = ""):
        self._keyword = keyword
        self._access_key = access_key or os.environ.get(
            "PORCUPINE_ACCESS_KEY", DEFAULT_ACCESS_KEY
        )
        self._porcupine = None
        self._audio_stream = None
        self._running = False
        self._callback: Optional[Callable] = None

    def set_keyword(self, keyword: str):
        """设置唤醒词 (内置关键词或 .ppn 文件路径)。"""
        self._keyword = keyword
        if self._porcupine:
            self._porcupine.delete()
            self._porcupine = None

    @property
    def is_available(self) -> bool:
        """检查 Porcupine 是否可用。"""
        try:
            import pvporcupine
            return True
        except ImportError:
            return False

    @property
    def display_name(self) -> str:
        """唤醒词显示名。"""
        return self.BUILTIN_KEYWORDS.get(self._keyword, self._keyword)

    def _init_engine(self):
        """初始化 Porcupine 引擎。"""
        if self._porcupine is not None:
            return

        import pvporcupine

        # 检查是否是内置关键词
        if self._keyword in pvporcupine.KEYWORDS:
            self._porcupine = pvporcupine.create(
                access_key=self._access_key,
                keywords=[self._keyword],
            )
        elif os.path.exists(self._keyword):
            # 自定义 .ppn 文件
            self._porcupine = pvporcupine.create(
                access_key=self._access_key,
                keyword_paths=[self._keyword],
            )
        else:
            raise ValueError(f"未知唤醒词: {self._keyword}")

        logger.info(
            f"WakeWord initialized: '{self._keyword}' "
            f"(frame_length={self._porcupine.frame_length}, "
            f"sample_rate={self._porcupine.sample_rate})"
        )

    async def listen(
        self, device_index: int = -1, audio_callback: Optional[Callable] = None
    ) -> AsyncGenerator[str, None]:
        """持续监听唤醒词。

        Args:
            device_index: 音频输入设备索引 (-1 = 默认)
            audio_callback: 可选, 每帧音频回调 (用于可视化/调试)

        Yields:
            "wake" — 检测到唤醒词
        """
        import struct
        import sounddevice as sd

        self._init_engine()
        self._running = True

        p = self._porcupine
        sample_rate = p.sample_rate
        frame_length = p.frame_length

        def audio_callback_inner(indata, frames, time_info, status):
            """sounddevice 回调 — 每帧检测一次唤醒词。"""
            if not self._running:
                raise sd.CallbackStop()

            # 转换音频数据
            audio_frame = struct.pack(
                "h" * len(indata), *(int(x * 32767) for x in indata[:, 0])
            )

            # Porcupine 检测
            result = p.process(audio_frame)
            if result >= 0:
                logger.info(f"Wake word detected! keyword_index={result}")
                # 触发回调
                if self._callback:
                    self._callback()

        try:
            with sd.InputStream(
                device=device_index,
                samplerate=sample_rate,
                blocksize=frame_length,
                channels=1,
                dtype="float32",
                callback=audio_callback_inner,
            ):
                while self._running:
                    await asyncio.sleep(0.1)
                    if self._callback:
                        self._callback = None
                        yield "wake"
        except Exception as e:
            logger.error(f"Wake word listen error: {e}")
        finally:
            self._running = False

    def stop(self):
        """停止监听。"""
        self._running = False

    def on_wake(self, callback: Callable):
        """注册唤醒回调。"""
        self._callback = callback

    def close(self):
        """释放资源。"""
        self.stop()
        if self._porcupine:
            self._porcupine.delete()
            self._porcupine = None


# 快捷方式: 检测是否可用
def is_wake_word_available() -> bool:
    """检查唤醒词功能是否可用。"""
    detector = WakeWordDetector()
    return detector.is_available
