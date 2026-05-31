"""Text-to-Speech engine — layered architecture.

Layer 0: Pre-recorded audio cache (常见短回应, 0ms 延迟)
Layer 1: edge-tts (在线, 高自然度, ~200ms 首音)
Layer 2: macOS say command (离线, 即时可用)

自动选择: 根据回复内容长度和设备能力自动选层。
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
from typing import AsyncGenerator, Optional

logger = logging.getLogger("butler.tts")


class PrerecordedAudio:
    """预录音频缓存 — 常见短回应直接返回, 零延迟。"""

    def __init__(self, cache_dir: str = ""):
        self.cache_dir = cache_dir
        self._cache: dict[str, bytes] = {}
        self._load_defaults()

    def _load_defaults(self):
        self._phrases = {
            "好的": "好的",
            "已打开": "已打开",
            "已关闭": "已关闭",
            "正在处理": "正在处理",
            "请再说一遍": "请再说一遍",
            "抱歉": "抱歉",
        }

    def get(self, text: str) -> Optional[bytes]:
        return self._cache.get(text)

    def add(self, text: str, audio: bytes):
        self._cache[text] = audio

    def match(self, text: str) -> Optional[str]:
        text_clean = text.strip()
        for phrase in self._phrases:
            if text_clean == phrase or text_clean.startswith(phrase):
                return phrase
        return None


class EdgeTTS:
    """Edge TTS — 微软语音合成 (在线, 高自然度)."""

    def __init__(self):
        self.voice = os.environ.get("TTS_VOICE", "zh-CN-XiaoxiaoNeural")
        self.rate = os.environ.get("TTS_RATE", "+0%")
        self.volume = os.environ.get("TTS_VOLUME", "+0%")

    async def synthesize(self, text: str) -> Optional[bytes]:
        """合成文本为完整 MP3 音频。"""
        try:
            import edge_tts
            communicate = edge_tts.Communicate(text, self.voice, rate=self.rate, volume=self.volume)
            audio = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio.extend(chunk["data"])
            return bytes(audio) if audio else None
        except Exception as e:
            logger.error(f"EdgeTTS error: {e}")
            return None

    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        """流式合成文本为 MP3 音频块 (边合成边产出)。

        Yields:
            bytes: MP3 音频片段, 第一个 chunk 包含 MP3 头信息
        """
        if not text.strip():
            return
        try:
            import edge_tts
            communicate = edge_tts.Communicate(text, self.voice, rate=self.rate, volume=self.volume)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    yield chunk["data"]
        except Exception as e:
            logger.error(f"EdgeTTS stream error: {e}")
            return


class MacOSTTS:
    """macOS say 命令 TTS (离线, 即时可用)."""

    def __init__(self):
        self.voice = "Tingting"

    async def synthesize(self, text: str) -> Optional[bytes]:
        loop = asyncio.get_event_loop()
        def _run():
            import uuid
            out_path = f"/tmp/hermes_tts_{uuid.uuid4().hex}.aiff"
            try:
                subprocess.run(["say", "-v", self.voice, "-o", out_path, text],
                               capture_output=True, timeout=30)
                with open(out_path, "rb") as f:
                    return f.read()
            except Exception as e:
                logger.error(f"MacOSTTS error: {e}")
                return None
            finally:
                subprocess.run(["rm", "-f", out_path], capture_output=True)
        return await loop.run_in_executor(None, _run)


class TTSEngine:
    """Layered TTS Engine — 自动选择最佳引擎。

    Layer 0: Pre-recorded cache (0ms)
    Layer 1: Edge TTS (在线, ~200ms 首音)
    Layer 2: macOS say (离线, 50ms)
    """

    def __init__(self):
        self.prerecorded = PrerecordedAudio()
        self.edge = EdgeTTS()
        self.macos = MacOSTTS()
        self._layer_hits = {"cache": 0, "edge": 0, "macos": 0}

    def is_available(self) -> bool:
        return True

    async def synthesize(self, text: str) -> tuple[Optional[bytes], str]:
        """合成文本为完整音频。

        Returns:
            (audio_bytes, format) — format: "mp3" or "aiff"
        """
        if not text.strip():
            return None, ""

        # Layer 0: Pre-recorded cache
        cached = self.prerecorded.match(text)
        if cached:
            audio = self.prerecorded.get(cached)
            if audio:
                self._layer_hits["cache"] += 1
                return audio, "mp3"

        # Layer 1: Edge TTS
        try:
            audio = await self.edge.synthesize(text)
            if audio:
                self._layer_hits["edge"] += 1
                return audio, "mp3"
        except Exception:
            pass

        # Layer 2: macOS say
        audio = await self.macos.synthesize(text)
        if audio:
            self._layer_hits["macos"] += 1
            return audio, "aiff"

        return None, ""

    async def synthesize_stream(self, text: str) -> AsyncGenerator[tuple[bytes, str], None]:
        """流式合成文本为音频块。

        Yields:
            (audio_chunk, format) — 片段+格式标识
        """
        if not text.strip():
            return

        # Layer 0: 预录音频直接作为完整块输出
        cached = self.prerecorded.match(text)
        if cached:
            audio = self.prerecorded.get(cached)
            if audio:
                self._layer_hits["cache"] += 1
                yield audio, "mp3"
                return

        # Layer 1: Edge TTS 流式
        try:
            first = True
            async for chunk in self.edge.synthesize_stream(text):
                if first:
                    self._layer_hits["edge"] += 1
                    first = False
                yield chunk, "mp3"
            if not first:
                return
        except Exception:
            pass

        # Layer 2: macOS say (完整合成后作为单块输出)
        audio = await self.macos.synthesize(text)
        if audio:
            self._layer_hits["macos"] += 1
            yield audio, "aiff"

    def get_stats(self) -> dict:
        total = sum(self._layer_hits.values()) or 1
        return {
            "cache_hits": self._layer_hits["cache"],
            "edge_hits": self._layer_hits["edge"],
            "macos_hits": self._layer_hits["macos"],
            "cache_rate": f"{self._layer_hits['cache'] / total * 100:.0f}%",
        }
