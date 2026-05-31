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
            communicate = edge_tts.Communicate(text, self.voice, rate=self.rate, volume=self.volume, connect_timeout=3)
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
            communicate = edge_tts.Communicate(
                text, self.voice,
                rate=self.rate, volume=self.volume,
                connect_timeout=3, receive_timeout=15
            )
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    yield chunk["data"]
        except Exception as e:
            logger.error(f"EdgeTTS stream error: {e}")
            return


class MacOSTTS:
    """macOS say 命令 TTS (离线, 本地引擎).

    支持:
    - AIFF 合成 (内置 say 命令)
    - ffmpeg 转码为 MP3 (浏览器兼容)
    - 常用短语缓存 (二次响应零延迟)
    - 并发非阻塞执行
    """

    def __init__(self):
        self.voice = "Tingting"  # macOS 中文女声
        self._cache: dict[str, bytes] = {}  # 预缓存
        self._cache_hits = 0
        self._cache_misses = 0
        self._ffmpeg_ok = self._check_ffmpeg()

    def _check_ffmpeg(self) -> bool:
        """检查 ffmpeg 是否可用。"""
        try:
            r = subprocess.run(["ffmpeg", "-version"],
                              capture_output=True, timeout=5)
            return r.returncode == 0
        except:
            return False

    @property
    def cache_hit_rate(self) -> str:
        total = self._cache_hits + self._cache_misses
        if total == 0:
            return "0%"
        return f"{self._cache_hits * 100 // total}%"

    async def synthesize(self, text: str) -> Optional[bytes]:
        """合成文本为 AIFF 音频。"""
        if not text.strip():
            return None

        # 检查缓存
        if text in self._cache:
            self._cache_hits += 1
            return self._cache[text]

        self._cache_misses += 1

        loop = asyncio.get_event_loop()
        def _run():
            import uuid
            out_path = f"/tmp/aria_tts_{uuid.uuid4().hex}.aiff"
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

        data = await loop.run_in_executor(None, _run)

        # 缓存短句
        if data and len(text) < 50:
            self._cache[text] = data

        return data

    async def synthesize_stream(self, text: str):
        """流式输出 — AIFF 合成后转 MP3。

        Yields:
            (mp3_chunk, "mp3") — 单块 MP3 音频
        """
        aiff_data = await self.synthesize(text)
        if not aiff_data:
            return

        # 检查 MP3 版本缓存
        cache_key = f"mp3:{text}"
        if cache_key in self._cache:
            yield self._cache[cache_key], "mp3"
            return

        if not self._ffmpeg_ok:
            # ffmpeg 不可用, 直接输出 AIFF
            yield aiff_data, "aiff"
            return

        # 写入临时文件, ffmpeg 转码
        import uuid
        aiff_path = f"/tmp/aria_tts_{uuid.uuid4().hex}.aiff"
        mp3_path = f"/tmp/aria_tts_{uuid.uuid4().hex}.mp3"

        try:
            # 写入 AIFF
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: open(aiff_path, "wb").write(aiff_data))

            # ffmpeg 转 MP3 (64kbps, 兼容性好)
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", aiff_path,
                "-codec:a", "libmp3lame", "-b:a", "64k",
                "-f", "mp3", mp3_path,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            await proc.wait()

            if os.path.exists(mp3_path):
                def _read_mp3():
                    with open(mp3_path, "rb") as f:
                        return f.read()
                mp3_data = await loop.run_in_executor(None, _read_mp3)

                # 缓存
                if mp3_data and len(text) < 50:
                    self._cache[cache_key] = mp3_data

                yield mp3_data, "mp3"
                return
        except Exception as e:
            logger.error(f"MacOSTTS ffmpeg error: {e}")
        finally:
            for p in [aiff_path, mp3_path]:
                try: os.remove(p)
                except: pass

        # 兜底
        yield aiff_data, "aiff"


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
