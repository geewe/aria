"""Multi-level Voice Activity Detection.

Architecture:
  Level 0: Energy-based (超轻量, 无依赖)
  Level 1: Spectral-based (numpy FFT, 更准确)
  Level 2: ML-based (Silero via ONNX, 最准确 — deferred)
  
  Fallback chain: L2 → L1 → L0 (L0 always available)
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from enum import Enum
from typing import Optional

import numpy as np

logger = logging.getLogger("butler.audio.vad")


class VADEvent(Enum):
    NONE = "none"
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"
    NOISE = "noise"  # 非人声的噪声


class VADConfig:
    """VAD configuration — fully tunable per environment."""
    # Frame
    sample_rate: int = 16000
    frame_ms: int = 30  # 每帧 30ms
    frame_size: int = sample_rate * frame_ms // 1000  # 480 samples

    # Energy thresholds (Level 0)
    energy_threshold: float = 0.015  # 基础能量阈值
    energy_adaptive_speed: float = 0.02  # 自适应速度

    # Speech detection
    min_speech_frames: int = 3  # 至少连续 3 帧才算 SPEECH_START (~90ms)
    min_silence_frames: int = 25  # 约 750ms 静音才算 SPEECH_END

    # Spectral VAD (Level 1)
    use_spectral: bool = True
    spectral_fft_size: int = 512

    # Noise floor tracking
    noise_floor_alpha: float = 0.05  # 噪声地板更新速度
    noise_floor_frames_init: int = 60  # 前 60 帧 (~1.8s) 用于初始化

    # Hangover (避免语音中间短暂停顿被误判为结束)
    hangover_frames: int = 5  # 语音结束后额外保持 5 帧 (~150ms)


class EnergyVAD:
    """Level 0: Pure energy-based VAD.

    基于信号能量 + 自适应噪声地板。
    零依赖, < 0.1ms 每帧。
    """

    def __init__(self, config: VADConfig | None = None):
        if config is None:
            config = VADConfig()
        self.config = config or VADConfig()
        self.noise_floor = 0.01
        self.noise_floor_frames = 0
        self._history = deque(maxlen=10)  # 最近 10 帧能量历史

    def reset(self):
        self.noise_floor = 0.01
        self.noise_floor_frames = 0
        self._history.clear()

    def _frame_energy(self, frame: np.ndarray) -> float:
        """计算一帧音频的能量 (RMS)。"""
        if len(frame) == 0:
            return 0.0
        return float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))

    def is_speech(self, frame: np.ndarray) -> tuple[bool, float]:
        """判断一帧是否包含语音。
        
        Returns:
            (is_speech, energy_level)
        """
        energy = self._frame_energy(frame)
        self._history.append(energy)

        # 噪声地板初始化
        if self.noise_floor_frames < self.config.noise_floor_frames_init:
            self.noise_floor = (
                self.noise_floor * 0.9 + energy * 0.1
            )
            self.noise_floor_frames += 1
            return False, energy

        # 自适应阈值: 噪声地板 * 2.5 + 偏置
        threshold = max(
            self.config.energy_threshold,
            self.noise_floor * 2.5 + 0.02,
        )

        is_speech_val = energy > threshold

        # 更新噪声地板 (只更新非语音帧)
        if not is_speech_val:
            self.noise_floor = (
                self.noise_floor * (1 - self.config.noise_floor_alpha)
                + energy * self.config.noise_floor_alpha
            )

        return is_speech_val, energy


class SpectralVAD:
    """Level 1: Spectral-based VAD using FFT.

    通过频谱特征判断语音 (语音有谐波结构, 噪声通常是平坦谱)。
    比纯能量法更准确, 能区分电视声和人声。
    """

    def __init__(self, config: VADConfig | None = None):
        if config is None:
            config = VADConfig()
        self.config = config or VADConfig()
        self.fft_size = self.config.spectral_fft_size
        self.noise_spectrum: Optional[np.ndarray] = None
        self.noise_frames = 0

    def reset(self):
        self.noise_spectrum = None
        self.noise_frames = 0

    def _compute_spectral_features(self, frame: np.ndarray) -> dict:
        """计算频谱特征。

        Returns:
            spectral_flatness: 频谱平坦度 (0=纯音, 1=白噪声)
            spectral_centroid: 频谱质心 (Hz)
            peak_to_avg: 峰值/均值比
        """
        if len(frame) < self.fft_size:
            frame = np.pad(frame, (0, self.fft_size - len(frame)))

        fft = np.fft.rfft(frame.astype(np.float64), n=self.fft_size)
        magnitude = np.abs(fft) + 1e-10
        power = magnitude ** 2
        total_power = np.sum(power)

        # 频谱平坦度
        log_avg = np.exp(np.mean(np.log(power)))
        spectral_flatness = log_avg / (total_power / len(power) + 1e-10)

        # 频谱质心
        freqs = np.fft.rfftfreq(self.fft_size, d=1.0 / self.config.sample_rate)
        spectral_centroid = np.sum(freqs * power) / total_power if total_power > 0 else 0

        # 峰值均值比
        peak_to_avg = np.max(magnitude) / (np.mean(magnitude) + 1e-10)

        return {
            "spectral_flatness": float(spectral_flatness),
            "spectral_centroid": float(spectral_centroid),
            "peak_to_avg": float(peak_to_avg),
        }

    def is_speech(self, frame: np.ndarray) -> tuple[bool, float]:
        """通过频谱特征判断是否语音。
        
        Returns:
            (is_speech, confidence)
        """
        if len(frame) < 64:  # 帧太短, 无法做频谱分析
            return False, 0.0

        features = self._compute_spectral_features(frame)
        sf = features["spectral_flatness"]
        sc = features["spectral_centroid"]
        p2a = features["peak_to_avg"]

        # 更新噪声谱 (前 30 帧用于初始化)
        if self.noise_frames < 30:
            self.noise_frames += 1
            return False, 0.0

        # 判断规则:
        # 1. 语音的频谱平坦度低 (有谐波结构): sf < 0.5
        # 2. 语音的峰值均值比高: p2a > 3
        # 3. 频谱质心在合理范围 (80-3000Hz 是人声范围)
        is_speech_val = (
            sf < 0.6
            and p2a > 2.5
            and 80 < sc < 3000
        )

        confidence = 1.0 - sf if is_speech_val else 0.0
        confidence = min(max(confidence, 0.0), 1.0)

        return is_speech_val, confidence


class MultiLevelVAD:
    """Multi-level VAD — 组合能量法和频谱法。

    如果可用, 还可以加载 Silero VAD (ONNX) 作为 Level 2。
    """

    def __init__(self, config: VADConfig | None = None):
        if config is None:
            config = VADConfig()
        self.config = config or VADConfig()
        self.energy_vad = EnergyVAD(config)
        self.spectral_vad = SpectralVAD(config) if config.use_spectral else None

        # State machine
        self.is_speaking = False
        self.speech_frames = 0
        self.silence_frames = 0
        self.hangover = 0

        # Smoothing
        self._recent_decisions: deque = deque(maxlen=5)

        logger.info(
            f"VAD initialized: energy + {'spectral' if self.spectral_vad else 'no spectral'}"
        )

    def reset(self):
        """Reset VAD state for new conversation."""
        self.energy_vad.reset()
        if self.spectral_vad:
            self.spectral_vad.reset()
        self.is_speaking = False
        self.speech_frames = 0
        self.silence_frames = 0
        self.hangover = 0
        self._recent_decisions.clear()

    def process_frame(self, audio_bytes: bytes) -> VADEvent:
        """处理一帧音频, 返回 VAD 事件。

        Args:
            audio_bytes: 16kHz 16bit PCM 音频帧 (960 bytes = 30ms)

        Returns:
            VADEvent
        """
        if len(audio_bytes) < 64:
            return VADEvent.NONE

        # Convert to float32
        frame = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        # Level 0: Energy VAD
        is_speech_energy, energy = self.energy_vad.is_speech(frame)

        # Level 1: Spectral VAD (optional)
        is_speech_spectral = False
        spectral_conf = 0.0
        if self.spectral_vad:
            is_speech_spectral, spectral_conf = self.spectral_vad.is_speech(frame)

        # 投票决策: energy 或 spectral
        is_active = is_speech_energy or is_speech_spectral

        # 如果两个都有, 置信度更高
        if is_speech_energy and is_speech_spectral:
            pass  # 双重确认

        # Smoothing
        self._recent_decisions.append(is_active)
        if len(self._recent_decisions) == self._recent_decisions.maxlen:
            # 至少 3/5 帧判定为语音才算
            smoothed_active = sum(self._recent_decisions) >= 3
        else:
            smoothed_active = is_active

        # 状态机
        if smoothed_active and not self.is_speaking:
            self.speech_frames += 1
            if self.speech_frames >= self.config.min_speech_frames:
                self.is_speaking = True
                self.silence_frames = 0
                self.hangover = self.config.hangover_frames
                logger.debug(
                    f"SPEECH_START (energy={energy:.4f}, "
                    f"spectral_conf={spectral_conf:.2f})"
                )
                return VADEvent.SPEECH_START
            return VADEvent.NONE

        elif not smoothed_active and self.is_speaking:
            self.silence_frames += 1
            # Hangover: 语音结束后保持 hangover_frames 帧
            if self.hangover > 0:
                self.hangover -= 1
                return VADEvent.NONE
            if self.silence_frames >= self.config.min_silence_frames:
                self.is_speaking = False
                self.speech_frames = 0
                logger.debug(
                    f"SPEECH_END (silence={self.silence_frames * self.config.frame_ms}ms)"
                )
                return VADEvent.SPEECH_END
            return VADEvent.NONE

        elif smoothed_active and self.is_speaking:
            # 持续语音中
            self.silence_frames = 0
            self.hangover = self.config.hangover_frames
            return VADEvent.NONE

        else:
            # 静默中且非语音
            if self.speech_frames > 0:
                self.speech_frames = 0
            return VADEvent.NONE


# Alias for backwards compatibility
VADProcessor = MultiLevelVAD
