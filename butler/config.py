"""Configuration for Aria 家庭助手 v4 — 支持 YAML 文件 + 环境变量覆盖。"""

import os
import yaml
from pathlib import Path
from typing import Any, Optional


class Config:
    """分层配置：YAML 文件 → 环境变量 → 默认值。"""

    def __init__(self):
        self._data: dict = {}
        self._load_yaml()
        self._apply_env_overrides()

    def _get_config_path(self) -> Path:
        """查找配置文件路径。"""
        candidates = [
            Path.cwd() / "config.yaml",
            Path.home() / ".aria" / "config.yaml",
            Path(__file__).parent.parent / "config.yaml",
        ]
        for p in candidates:
            if p.exists():
                return p
        return candidates[0]

    def _load_yaml(self):
        """从 YAML 加载配置。"""
        path = self._get_config_path()
        if path.exists():
            with open(path) as f:
                self._data = yaml.safe_load(f) or {}
        else:
            self._data = {}

    def _get(self, *keys: str, default: Any = None) -> Any:
        """按路径获取配置值。"""
        d = self._data
        for k in keys:
            if isinstance(d, dict):
                d = d.get(k, {})
            else:
                return default
        return d if d != {} else default

    def _apply_env_overrides(self):
        """环境变量覆盖 — 优先级最高。"""

        # Server
        self._host = os.environ.get("ARIA_HOST") or self._get("server", "host", default="0.0.0.0")
        self._port = int(os.environ.get("ARIA_PORT") or self._get("server", "port", default=8653))

        # SSL
        self._ssl_cert = self._get("server", "ssl", "cert_file", default="cert.pem")
        self._ssl_key = self._get("server", "ssl", "key_file", default="key.pem")

        # LLM
        self._llm_api_url = os.environ.get("LLM_API_URL") or self._get("llm", "api_url", default="http://localhost:8642/v1/chat/completions")
        self._llm_api_key = os.environ.get("LLM_API_KEY") or self._get("llm", "api_key", default="hermes-lan-key")
        self._llm_model = os.environ.get("LLM_MODEL") or self._get("llm", "model", default="deepseek-v4-flash")
        self._llm_max_tokens = int(os.environ.get("LLM_MAX_TOKENS") or self._get("llm", "max_tokens", default=200))
        self._llm_timeout = int(os.environ.get("LLM_TIMEOUT") or self._get("llm", "timeout", default=5))
        self._llm_temperature = float(os.environ.get("LLM_TEMPERATURE") or self._get("llm", "temperature", default=0.7))

        # HomeAssistant
        self._hass_url = os.environ.get("HASS_URL") or self._get("homeassistant", "url", default="http://homeassistant.local:8123")
        self._hass_token = os.environ.get("HASS_TOKEN") or self._get("homeassistant", "token", default="")

        # TTS
        self._tts_voice = os.environ.get("TTS_VOICE") or self._get("tts", "voice", default="zh-CN-XiaoxiaoNeural")
        self._tts_rate = os.environ.get("TTS_RATE") or self._get("tts", "rate", default="+0%")
        self._tts_volume = os.environ.get("TTS_VOLUME") or self._get("tts", "volume", default="+0%")

        # Conversation
        self._conv_timeout = float(os.environ.get("CONV_TIMEOUT") or self._get("conversation", "timeout", default=8.0))
        self._max_context_turns = int(os.environ.get("MAX_CONTEXT_TURNS") or self._get("conversation", "max_context_turns", default=5))

        # Paths
        data_dir = os.environ.get("ARIA_DATA_DIR") or str(Path.home() / ".aria")
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

    # === Properties ===

    @property
    def HOST(self) -> str: return self._host

    @property
    def PORT(self) -> int: return self._port

    @property
    def SSL_CERT(self) -> str: return self._ssl_cert

    @property
    def SSL_KEY(self) -> str: return self._ssl_key

    @property
    def HERMES_API_URL(self) -> str: return self._llm_api_url

    @property
    def HERMES_API_KEY(self) -> str: return self._llm_api_key

    @property
    def LLM_MODEL(self) -> str: return self._llm_model

    @property
    def LLM_MAX_TOKENS(self) -> int: return self._llm_max_tokens

    @property
    def LLM_TIMEOUT(self) -> int: return self._llm_timeout

    @property
    def LLM_TEMPERATURE(self) -> float: return self._llm_temperature

    @property
    def HASS_URL(self) -> str: return self._hass_url

    @property
    def HASS_TOKEN(self) -> str: return self._hass_token

    @property
    def TTS_VOICE(self) -> str: return self._tts_voice

    @property
    def TTS_RATE(self) -> str: return self._tts_rate

    @property
    def TTS_VOLUME(self) -> str: return self._tts_volume

    @property
    def CONVERSATION_TIMEOUT(self) -> float: return self._conv_timeout

    @property
    def MAX_CONTEXT_TURNS(self) -> int: return self._max_context_turns

    @property
    def DATA_DIR(self) -> Path: return self._data_dir

    @property
    def AUDIO_CACHE(self) -> Path:
        p = self._data_dir / "audio_cache"
        p.mkdir(exist_ok=True)
        return p

    @property
    def LOG_FILE(self) -> Path:
        return self._data_dir / "aria.log"

    @property
    def SAMPLE_RATE(self) -> int: return 16000
    @property
    def FRAME_MS(self) -> int: return 30
    @property
    def FRAME_SIZE(self) -> int: return int(16000 * 30 / 1000)
    @property
    def FRAME_BYTES(self) -> int: return self.FRAME_SIZE * 2

    # System prompt
    @property
    def SYSTEM_PROMPT(self) -> str:
        return """你是Aria,一个智能家庭语音助手。
规则:
- 回复口语化,简短自然,像真人说话
- 不列点,不用markdown,不用emoji,不用特殊符号
- 确认操作要简短:"好的""已经打开了""设置了26度"
- 查询类要直接给答案:"今天25到30度,晴"
- 不要说"我来帮你查询一下"这种客套废话
- 多轮对话中记住上下文
- 如果没听懂说"能再说一遍吗"
- 用户问不会的东西诚实说"这个我还不清楚"
- 当前时间: {time}
- 当前用户: {user}"""

    # === Config management ===

    def to_dict(self) -> dict:
        """导出配置字典（用于 API）。"""
        return {
            "version": "4.1.0",
            "server": {
                "host": self.HOST,
                "port": self.PORT,
            },
            "llm": {
                "provider": "hermes",
                "api_url": self.HERMES_API_URL,
                "model": self.LLM_MODEL,
                "max_tokens": self.LLM_MAX_TOKENS,
                "timeout": self.LLM_TIMEOUT,
                "temperature": self.LLM_TEMPERATURE,
            },
            "homeassistant": {
                "url": self.HASS_URL,
                "connected": bool(self.HASS_TOKEN),
            },
            "tts": {
                "voice": self.TTS_VOICE,
                "rate": self.TTS_RATE,
                "volume": self.TTS_VOLUME,
            },
            "conversation": {
                "timeout": self.CONVERSATION_TIMEOUT,
                "max_context_turns": self.MAX_CONTEXT_TURNS,
            },
            "data_dir": str(self.DATA_DIR),
        }


config = Config()
