"""Acoustic Echo Cancellation (AEC) — 声学回声消除。

使用 NLMS (Normalized Least Mean Squares) 自适应滤波器。
参考信号: 当前 TTS 播放的音频
近端信号: 麦克风采集的混合信号 (人声 + 回声)

架构:
  1. 主 AEC: NLMS 自适应滤波器 (128ms 尾部)
  2. 双讲检测 (Double-Talk Detector): 防止发散
  3. 残留回声抑制 (Residual Echo Suppression)
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Optional

import numpy as np

logger = logging.getLogger("butler.audio.aec")


class AcousticEchoCanceller:
    """NLMS-based Acoustic Echo Canceller.

    Args:
        filter_length: 滤波器长度 (taps), 对应回声尾部长度
            2048 taps @ 16kHz = 128ms 尾部 (覆盖大部分家庭场景)
        step_size: NLMS 步长 (0.1-0.5, 越大收敛越快但稳定性差)
        sample_rate: 音频采样率
    """

    def __init__(
        self,
        filter_length: int = 2048,
        step_size: float = 0.3,
        sample_rate: int = 16000,
    ):
        self.filter_length = filter_length
        self.step_size = step_size
        self.sample_rate = sample_rate

        # 自适应滤波器系数
        self._w = np.zeros(filter_length, dtype=np.float64)

        # 远端参考信号缓冲
        self._ref_buffer = deque(maxlen=filter_length)

        # 双讲检测
        self._double_talk_count = 0
        self._dt_threshold = 2.0  # 双讲判定阈值 (能量比)

        # 收敛状态
        self._converged = False
        self._convergence_frames = 0
        self._min_convergence_frames = 300  # ~300ms 初始收敛

        # 性能统计
        self._erl = 0.0  # Echo Return Loss (dB)
        self._rerl = 0.0  # Residual ERL

        logger.info(
            f"AEC initialized: {filter_length}taps "
            f"({filter_length / sample_rate * 1000:.0f}ms tail)"
        )

    def reset(self):
        """Reset filter state (e.g., after a large echo path change)."""
        self._w.fill(0.0)
        self._ref_buffer.clear()
        self._double_talk_count = 0
        self._converged = False
        self._convergence_frames = 0

    @property
    def is_converged(self) -> bool:
        """滤波器是否已收敛。"""
        return self._converged

    @property
    def echo_return_loss(self) -> float:
        """回声返回损耗 (dB), 越大越好。"""
        return self._erl

    def set_reference(self, audio_chunk: bytes) -> None:
        """设置远端参考信号 (TTS 播放的音频)。
        
        在 TTS 开始播放时调用。
        """
        ref = np.frombuffer(audio_chunk, dtype=np.int16).astype(np.float64)
        self._ref_buffer.extend(ref.tolist())

    def _detect_double_talk(
        self, near: np.ndarray, far: np.ndarray, error: np.ndarray
    ) -> bool:
        """双讲检测: 近端和远端同时有信号时冻结自适应。

        使用 Geigel 算法:
        - 如果 |near| > threshold * max(|far|) → 双讲
        """
        near_peak = np.max(np.abs(near))
        far_peak = np.max(np.abs(far))

        if far_peak < 100:  # 远端无信号 → 无双讲
            return False

        ratio = near_peak / (far_peak + 1e-10)
        return ratio > self._dt_threshold

    def process(self, near_signal: bytes) -> bytes:
        """处理一帧近端信号 (麦克风输入), 返回消除回声后的信号。

        Args:
            near_signal: 16kHz 16bit PCM, 单帧 (30ms = 960 bytes)

        Returns:
            processed: 消除回声后的 PCM bytes

        如果没有参考信号 (TTS未播放), 直通返回。
        """
        if len(self._ref_buffer) < self.filter_length:
            # 滤波器还没填满, 直通
            return near_signal

        # 转换为 float64
        near = np.frombuffer(near_signal, dtype=np.int16).astype(np.float64)

        # 构建参考信号向量
        far = np.array(list(self._ref_buffer)[-self.filter_length:], dtype=np.float64)

        # NLMS 滤波
        # y(n) = w^T * x(n)
        est_echo = np.dot(self._w, far)

        # e(n) = d(n) - y(n)
        error = near - est_echo

        # 双讲检测
        is_double_talk = self._detect_double_talk(near, far, error)

        if not is_double_talk:
            # NLMS 更新
            # w(n+1) = w(n) + mu * e(n) * x(n) / (x^T * x + epsilon)
            far_power = np.dot(far, far) + 1e-10
            normalization = self.step_size / far_power
            self._w += normalization * error * far

            # 限制滤波器系数 (防止发散)
            norm = np.linalg.norm(self._w)
            if norm > 100:
                self._w *= 100 / norm

            # 收敛计数
            if not self._converged:
                self._convergence_frames += 1
                if self._convergence_frames >= self._min_convergence_frames:
                    self._converged = True
                    logger.info("AEC converged")
        else:
            self._double_talk_count += 1

        # 残留回声抑制: 软掩蔽
        # 如果误差信号远小于近端信号 (说明被抑制得好), 保持
        # 否则做额外的非线性处理
        near_power = np.mean(near ** 2) + 1e-10
        error_power = np.mean(error ** 2) + 1e-10

        erl = 10 * np.log10(near_power / error_power) if error_power > 0 else 0
        self._erl = self._erl * 0.9 + erl * 0.1

        # 如果 ER 太低 (< 3dB), 做中心削波
        if self._erl < 3.0 and near_power > 1000:
            # 软削波: 低于阈值归零
            threshold = np.std(error) * 0.5
            error = np.where(np.abs(error) < threshold, 0, error)

        # 转回 int16
        processed = np.clip(error, -32768, 32767).astype(np.int16)
        return processed.tobytes()

    def get_stats(self) -> dict:
        """获取 AEC 统计信息。"""
        return {
            "converged": self._converged,
            "convergence_frames": self._convergence_frames,
            "erl_db": round(self._erl, 1),
            "double_talk_count": self._double_talk_count,
            "filter_norm": round(float(np.linalg.norm(self._w)), 2),
        }
