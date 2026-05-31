"""Speech-to-Text engine — multi-engine with fallback chain.

Engine chain:
  Level 0: SenseVoice (本地, 离线, 中文最优)
  Level 1: edge-tts STT (在线, 备选)
  Level 2: faster-whisper (本地, 离线, 通用)

热词增强: 从 HA 实体名自动生成热词列表
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import AsyncGenerator, Optional

import numpy as np

logger = logging.getLogger("butler.stt")


class STTResult:
    """STT 识别结果。"""
    def __init__(self, text: str, is_final: bool, language: str = "zh",
                 emotion: str = "neutral", confidence: float = 0.0):
        self.text = text
        self.is_final = is_final
        self.language = language
        self.emotion = emotion
        self.confidence = confidence


class HotwordManager:
    """热词管理器 — 从 HA 实体名自动生成热词。"""
    
    def __init__(self):
        self.hotwords: set[str] = set()
    
    def add_from_ha_entities(self, entities: list[dict]):
        """从 HA 实体列表添加热词。
        
        Args:
            entities: HA 实体列表, 每项包含 friendly_name, entity_id
        """
        for ent in entities:
            name = ent.get("friendly_name", "")
            if name:
                # "客厅主灯" → ["客厅主灯", "主灯"]
                self.hotwords.add(name)
                # 去掉地点前缀
                parts = name.split()
                if len(parts) > 1:
                    self.hotwords.add(parts[-1])
            
            # 从 entity_id 提取
            eid = ent.get("entity_id", "")
            if "_" in eid:
                name_from_id = eid.split(".", 1)[-1].replace("_", " ")
                self.hotwords.add(name_from_id)
        
        logger.info(f"Hotwords loaded: {len(self.hotwords)} words")
    
    def get_hotwords(self) -> list[str]:
        return list(self.hotwords)


class SenseVoiceSTT:
    """SenseVoice STT — 中文语音识别最优选择。
    
    通过调用 FunASR 或 SenseVoice 的 Python API。
    如果不可用, 自动降级到备选引擎。
    """
    
    def __init__(self):
        self.model = None
        self._load_model()
    
    def _load_model(self):
        """Lazy-load SenseVoice model."""
        try:
            from funasr import AutoModel
            self.model = AutoModel(
                model="iic/SenseVoiceSmall",
                device="cpu",
                disable_update=True,
            )
            logger.info("SenseVoice model loaded (Small, 160MB)")
        except Exception as e:
            logger.warning(f"SenseVoice not available: {e}")
            self.model = None
    
    def is_available(self) -> bool:
        return self.model is not None
    
    def transcribe_sync(self, audio_np: np.ndarray, hotwords: list[str] = None) -> str:
        """同步转写 (在线程池运行)。"""
        if self.model is None:
            return ""
        
        try:
            result = self.model.generate(
                audio_np,
                language="zh",
                use_itn=True,  # 数字/标点归一化
                ban_phrase=None,
                hotwords=hotwords or [],
            )
            return result[0]["text"].strip() if result else ""
        except Exception as e:
            logger.error(f"SenseVoice error: {e}")
            return ""


class EdgeSTT:
    """Edge TTS 的语音识别 (在线, 备选)。"""
    
    def __init__(self):
        pass
    
    def is_available(self) -> bool:
        return True  # HTTP API, always available if network is up
    
    async def transcribe(self, audio_bytes: bytes, language: str = "zh-CN") -> str:
        """转写音频 — 优先 OpenAI Whisper API, 回退到 Google Free STT。"""
        # 尝试 OpenAI Whisper API (需要 OPENAI_API_KEY)
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if api_key:
            import httpx
            base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
                    data = {"model": "whisper-1", "language": language}
                    resp = await client.post(
                        f"{base_url}/audio/transcriptions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        files=files, data=data,
                    )
                    if resp.status_code == 200:
                        text = resp.json().get("text", "").strip()
                        if text:
                            return text
            except Exception as e:
                logger.warning(f"Whisper API error: {e}")

        # 回退: Google Free STT (SpeechRecognition)
        try:
            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(None, self._google_stt, audio_bytes, language)
            if text:
                return text
        except Exception as e:
            logger.warning(f"Google STT fallback error: {e}")

        return ""

    def _google_stt(self, audio_bytes: bytes, language: str = "zh-CN") -> str:
        """同步 Google STT (在 executor 中运行)。"""
        import numpy as np
        import speech_recognition as sr

        audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        pcm = (audio_np * 32767).astype(np.int16).tobytes()
        audio_data = sr.AudioData(pcm, 16000, 2)

        recognizer = sr.Recognizer()
        try:
            text = recognizer.recognize_google(audio_data, language=language)
            return text.strip()
        except sr.UnknownValueError:
            return ""
        except Exception as e:
            logger.warning(f"GoogleSTT error: {e}")
            return ""


class FasterWhisperSTT:
    """faster-whisper 本地 STT (通用备选)。"""
    
    def __init__(self):
        self.model = None
    
    def _load(self):
        if self.model is not None:
            return
        try:
            from faster_whisper import WhisperModel
            self.model = WhisperModel(
                "tiny", device="cpu", compute_type="int8",
                cpu_threads=4, num_workers=1,
            )
            logger.info("faster-whisper loaded (tiny, int8)")
        except Exception as e:
            logger.warning(f"faster-whisper not available: {e}")
    
    def is_available(self) -> bool:
        try:
            self._load()
            return self.model is not None
        except Exception:
            return False


class GoogleSTT:
    """SpeechRecognition Google STT — 免费, 无需 API Key。

    使用 Google Web Speech API 进行语音识别。
    需要网络连接, 在中国大陆可能被屏蔽。

    回退方案: 当此 STT 不可用或超时时, 返回空字符串。
    """

    def __init__(self):
        self._recognizer = None

    def _get(self):
        if self._recognizer is None:
            import speech_recognition as sr
            self._recognizer = sr.Recognizer()
        return self._recognizer

    def transcribe_sync(self, audio_np, language="zh-CN") -> str:
        """同步转写: 输入 numpy float32 数组, 输出文本。"""
        import numpy as np
        r = self._get()

        # Convert float32 [-1, 1] to int16 PCM
        pcm = (audio_np * 32767).astype(np.int16).tobytes()
        audio_data = __import__("speech_recognition", fromlist=["AudioData"]).AudioData(
            pcm, 16000, 2
        )

        try:
            text = r.recognize_google(audio_data, language=language)
            return text.strip()
        except __import__("speech_recognition").UnknownValueError:
            return ""
        except Exception as e:
            logger.warning(f"GoogleSTT error: {e}")
            return ""

    def is_available(self) -> bool:
        try:
            import speech_recognition
            return True
        except ImportError:
            return False


class FallbackSTT:
    """多层回退 STT — 尝试多个引擎直到成功。

    Engine 0: GoogleSTT (免费在线)
    Engine 1: 空 (返回空字符串, 触发 "请再说一遍")
    """

    def __init__(self):
        self._engines = []
        g = GoogleSTT()
        if g.is_available():
            self._engines.append(("google", g))
        logger.info(f"FallbackSTT: {len(self._engines)} engine(s) loaded")

    def transcribe(self, audio_bytes: bytes, language: str = "zh-CN") -> str:
        """转写音频字节。"""
        import numpy as np
        audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        for name, engine in self._engines:
            try:
                text = engine.transcribe_sync(audio_np, language)
                if text:
                    logger.info(f"STT [{name}]: {text[:200]}")
                    return text
            except Exception as e:
                logger.warning(f"STT [{name}] failed: {e}")
                continue

        return ""
    
    def transcribe_sync(self, audio_np: np.ndarray, language: str = "zh") -> str:
        self._load()
        if self.model is None:
            return ""
        try:
            segments, _ = self.model.transcribe(
                audio_np, language=language,
                beam_size=5, vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=300, threshold=0.5),
            )
            return " ".join(seg.text for seg in segments).strip()
        except Exception as e:
            logger.error(f"faster-whisper error: {e}")
            return ""


class StreamingSTT:
    """Multi-engine STT with fallback chain.
    
    Pipeline:
      1. SenseVoice (本地, 中文最优, 流式)
      2. EdgeSTT (在线, HTTP API)
      3. faster-whisper (本地, 离线通用)
    """
    
    def __init__(self):
        self.sense_voice = SenseVoiceSTT()
        self.edge_stt = EdgeSTT()
        self.fast_whisper = FasterWhisperSTT()
        self.hotwords = HotwordManager()
        
        # Streaming state
        self.audio_buffer: list[bytes] = []
        self.processing_lock = asyncio.Lock()
        
        logger.info(
            f"STT engines: "
            f"SenseVoice={'✅' if self.sense_voice.is_available() else '❌'} "
            f"Whisper={'✅' if self.fast_whisper.is_available() else '❌'}"
        )
    
    def is_available(self) -> bool:
        return (
            self.sense_voice.is_available()
            or self.fast_whisper.is_available()
        )
    
    def _pcm_to_float32(self, audio_bytes: bytes) -> np.ndarray:
        return np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    
    async def transcribe(self, audio_bytes: bytes, language: str = "zh") -> str:
        """转写完整音频段 (VAD 判定 SPEECH_END 后调用)。
        
        自动选择最佳可用引擎。
        """
        audio_np = self._pcm_to_float32(audio_bytes)
        hotwords = self.hotwords.get_hotwords()
        
        # Level 0: SenseVoice
        if self.sense_voice.is_available():
            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(
                None, self.sense_voice.transcribe_sync, audio_np, hotwords
            )
            if text:
                return text
        
        # Level 1: Edge STT (online)
        if len(audio_bytes) < 10 * 1024 * 1024:  # < 10MB
            text = await self.edge_stt.transcribe(audio_bytes, language)
            if text:
                return text
        
        # Level 2: faster-whisper
        if self.fast_whisper.is_available():
            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(
                None, self.fast_whisper.transcribe_sync, audio_np, language
            )
            if text:
                return text
        
        return ""
    
    async def transcribe_stream(
        self, audio_queue: asyncio.Queue, language: str = "zh"
    ) -> AsyncGenerator[STTResult, None]:
        """流式转写 — 实时输出中间结果。
        
        从队列消费音频帧, 每 500ms 输出一次中间结果。
        """
        async with self.processing_lock:
            all_audio = []
            partial_text = ""
            
            while True:
                try:
                    frame = await asyncio.wait_for(audio_queue.get(), timeout=0.5)
                    if frame is None:
                        break
                    all_audio.append(frame)
                except asyncio.TimeoutError:
                    if all_audio:
                        combined = b"".join(all_audio)
                        if len(combined) > 4800:  # 至少 300ms
                            text = await self.transcribe(combined, language)
                            if text and text != partial_text:
                                partial_text = text
                                yield STTResult(text, is_final=False)
                    continue
            
            # 最终结果
            if all_audio:
                text = await self.transcribe(b"".join(all_audio), language)
                if text:
                    yield STTResult(text, is_final=True)
                else:
                    yield STTResult("", is_final=True)
    
    def load_hotwords_from_ha(self, entities: list[dict]):
        """从 HA 实体列表加载热词。"""
        self.hotwords.add_from_ha_entities(entities)
