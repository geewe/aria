"""Configuration for Hermes Butler Core."""
import os
from pathlib import Path

class Config:
    # Server
    HOST = "0.0.0.0"
    PORT = 8650
    RELOAD = True

    # Hermes API (LLM backend)
    HERMES_API_URL = "http://localhost:8642/v1/chat/completions"
    HERMES_API_KEY = "hermes-lan-key"

    # HomeAssistant
    HASS_URL = "http://192.168.2.45:8123"
    HASS_TOKEN = os.environ.get("HASS_TOKEN", "")

    # Agent Platform
    AGENT_PLATFORM_URL = "http://localhost:8643"

    # Audio
    SAMPLE_RATE = 16000
    FRAME_MS = 30                        # 每帧 30ms
    FRAME_SIZE = int(SAMPLE_RATE * FRAME_MS / 1000)  # 480 samples
    FRAME_BYTES = FRAME_SIZE * 2         # 16bit = 2 bytes per sample

    # VAD
    VAD_THRESHOLD = 0.5
    VAD_MIN_SPEECH_MS = 150
    VAD_MIN_SILENCE_MS = 800            # 判定说话结束的静音时长
    VAD_HISTORY_FRAMES = 10

    # LLM
    LLM_MODEL = "deepseek-v4-flash"
    LLM_MAX_TOKENS = 200
    LLM_TIMEOUT = 15
    LLM_TEMPERATURE = 0.7

    # Conversation
    CONVERSATION_TIMEOUT = 8.0          # 免唤醒对话超时(秒)
    MAX_CONTEXT_TURNS = 5

    # TTS
    TTS_SPEED = 1.0
    TTS_VOLUME = 1.0
    TTS_SAMPLE_RATE = 24000

    # Paths
    DATA_DIR = Path.home() / ".hermes-butler"
    DATA_DIR.mkdir(exist_ok=True)
    LOG_FILE = DATA_DIR / "butler.log"
    AUDIO_CACHE = DATA_DIR / "audio_cache"
    AUDIO_CACHE.mkdir(exist_ok=True)

    # System prompt for voice mode
    SYSTEM_PROMPT = """你是Hermes管家,一个智能家庭语音助手。
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

config = Config()
