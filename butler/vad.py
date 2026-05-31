"""Voice Activity Detection using Silero VAD."""
from __future__ import annotations
import logging
import numpy as np
from enum import Enum

logger = logging.getLogger("butler.vad")


class VADEvent(Enum):
    NONE = 0
    SPEECH_START = 1
    SPEECH_END = 2


class VADProcessor:
    """Silero VAD wrapper with adaptive threshold."""

    def __init__(self):
        self.model = None
        self._load_model()

        # Parameters
        self.threshold = 0.5
        self.min_speech_frames = 5       # 5 frames × 30ms = 150ms
        self.min_silence_frames = 27     # 27 frames × 30ms ≈ 800ms

        # State
        self.speech_history: list[float] = []
        self.is_speaking = False
        self.speech_frames_count = 0
        self.silence_frames_count = 0
        self.noise_floor = 0.1
        self.initial_noise_est_frames = 0

    def _load_model(self):
        """Load Silero VAD via ONNX if available, fall back to energy-based."""
        import os
        model_path = os.path.expanduser("~/.hermes-butler/models/silero_vad.onnx")
        if os.path.exists(model_path):
            try:
                import onnxruntime
                self.ort_session = onnxruntime.InferenceSession(model_path)
                self.ort_input = self.ort_session.get_inputs()[0].name
                logger.info(f"Silero VAD loaded (ONNX, {os.path.getsize(model_path)//1024}KB)")
                return
            except Exception as e:
                logger.warning(f"Silero VAD ONNX load failed: {e}")

        logger.warning("Silero VAD not available, using energy-based VAD")
        self.model = None

    def _energy_vad(self, audio_chunk: np.ndarray) -> float:
        """Fallback energy-based VAD when Silero is not available."""
        if len(audio_chunk) == 0:
            return 0.0
        rms = np.sqrt(np.mean(audio_chunk ** 2))
        # Convert to probability-like value
        prob = min(1.0, rms * 50)
        return prob

    def process_frame(self, audio_bytes: bytes, timestamp: float = 0.0) -> VADEvent:
        """Process a 30ms audio frame and return VAD event."""
        # Convert bytes to float32 array
        audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        if len(audio_np) == 0:
            return VADEvent.NONE

        # Get speech probability
        if hasattr(self, 'ort_session') and self.ort_session is not None:
            try:
                # ONNX Silero VAD expects (1, 1, N) float32 input
                inp = audio_np.reshape(1, 1, -1)
                prob = self.ort_session.run(None, {self.ort_input: inp})[0].item()
            except Exception:
                prob = self._energy_vad(audio_np)
        elif self.model is not None:
            try:
                import torch
                audio_tensor = torch.from_numpy(audio_np)
                prob = self.model(audio_tensor, 16000).item()
            except Exception:
                prob = self._energy_vad(audio_np)
        else:
            prob = self._energy_vad(audio_np)

        # Update noise floor (first 30 frames or when not speaking)
        if self.initial_noise_est_frames < 30 and not self.is_speaking:
            if prob < self.noise_floor * 2:
                self.noise_floor = self.noise_floor * 0.9 + prob * 0.1
            self.initial_noise_est_frames += 1

        # Adaptive threshold
        adjusted_threshold = max(self.threshold, self.noise_floor * 3 + 0.1)
        is_active = prob > adjusted_threshold

        # Track history
        self.speech_history.append(prob)
        if len(self.speech_history) > 30:
            self.speech_history.pop(0)

        # State machine
        if is_active and not self.is_speaking:
            self.speech_frames_count += 1
            if self.speech_frames_count >= self.min_speech_frames:
                self.is_speaking = True
                self.silence_frames_count = 0
                logger.debug(f"SPEECH_START (prob={prob:.3f}, threshold={adjusted_threshold:.3f})")
                return VADEvent.SPEECH_START
            return VADEvent.NONE

        elif not is_active and self.is_speaking:
            self.silence_frames_count += 1
            if self.silence_frames_count >= self.min_silence_frames:
                self.is_speaking = False
                self.speech_frames_count = 0
                logger.debug(f"SPEECH_END (silence={self.silence_frames_count} frames)")
                return VADEvent.SPEECH_END
            return VADEvent.NONE

        elif is_active and self.is_speaking:
            self.silence_frames_count = 0
            return VADEvent.NONE

        else:
            # Not speaking and not active: reset speech counter
            if self.speech_frames_count > 0:
                self.speech_frames_count = 0
            return VADEvent.NONE

    def reset(self):
        """Reset VAD state for new conversation."""
        self.speech_history.clear()
        self.is_speaking = False
        self.speech_frames_count = 0
        self.silence_frames_count = 0
