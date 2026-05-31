"""Device session management — v4 distributed state.

每个设备有独立的状态机 + CRDT 状态同步。
支持多设备协同、对话上下文迁移。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

logger = logging.getLogger("butler.session")


class DeviceState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    ROUTING = "routing"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    WHISPER = "whisper"
    SILENT = "silent"
    DND = "do_not_disturb"


class DeviceType(Enum):
    SPEAKER = "speaker"
    TABLET = "tablet"
    PHONE = "phone"
    ESP32 = "esp32"
    UNKNOWN = "unknown"


@dataclass
class ConversationTurn:
    """单轮对话记录。"""
    user_text: str = ""
    assistant_text: str = ""
    action: str = ""
    intent: str = ""
    timestamp: float = 0.0
    latency_ms: int = 0
    user_id: str = "default"
    device_id: str = ""


@dataclass
class ConversationContext:
    """对话上下文 — 支持跨设备迁移的 CRDT 状态。"""
    session_id: str = ""
    turns: list[ConversationTurn] = field(default_factory=list)
    current_user: str = "default"
    current_room: str = "客厅"
    last_entity: str = ""
    last_intent: str = ""
    last_device: str = ""
    last_activity: float = 0.0
    expires_at: float = 0.0
    version: int = 0
    
    def add_turn(self, turn: ConversationTurn):
        self.turns.append(turn)
        self.last_activity = time.time()
        self.last_device = turn.device_id
        self.last_intent = turn.intent
        self.version += 1
        
        # 只保留最近 10 轮
        if len(self.turns) > 10:
            self.turns = self.turns[-10:]
    
    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "turns": [
                {"user_text": t.user_text, "assistant_text": t.assistant_text,
                 "action": t.action, "intent": t.intent, "timestamp": t.timestamp}
                for t in self.turns[-3:]  # 同步时只发最近 3 轮
            ],
            "current_user": self.current_user,
            "current_room": self.current_room,
            "last_entity": self.last_entity,
            "last_intent": self.last_intent,
            "last_device": self.last_device,
            "last_activity": self.last_activity,
            "expires_at": self.expires_at,
            "version": self.version,
        }


class DeviceSession:
    """单个设备连接。"""
    
    def __init__(self, websocket, device_id: str):
        self.ws = websocket
        self.device_id = device_id
        self.device_type = DeviceType.UNKNOWN
        self.room = "unknown"
        self.state = DeviceState.IDLE
        self.user = "default"
        self.user_id = "default"
        
        # Audio queues
        self.audio_buffer: asyncio.Queue[bytes] = asyncio.Queue()
        self.tts_queue: asyncio.Queue[dict] = asyncio.Queue()
        self.control_queue: asyncio.Queue[dict] = asyncio.Queue()
        
        # Interrupt
        self.interrupt_event = asyncio.Event()
        
        # Conversation
        self.context = ConversationContext(session_id=uuid.uuid4().hex[:12])
        self.conversation_timer: Optional[asyncio.Task] = None
        
        # TTS playback state (for AEC reference)
        self.is_speaking = False
        self.current_tts_task: Optional[asyncio.Task] = None
        self.last_tts_audio: Optional[bytes] = None
        
        # Metadata
        self.connected_at = datetime.now()
        self.last_heartbeat = datetime.now()
        self.firmware_version = ""
        
        # Capabilities (设备能力声明)
        self.capabilities = {
            "aec": False,       # 硬件AEC
            "mic_array": False,  # 麦克风阵列
            "screen": False,    # 有屏幕
            "local_vad": True,  # 本地VAD
            "local_stt": False,  # 本地STT
        }
    
    async def send_json(self, data: dict):
        try:
            await self.ws.send_json(data)
        except Exception:
            logger.warning(f"[{self.device_id}] send_json failed")
    
    async def send_audio(self, data: bytes):
        try:
            await self.ws.send_bytes(data)
        except Exception:
            logger.warning(f"[{self.device_id}] send_audio failed")
    
    async def set_state(self, new_state: DeviceState):
        old = self.state
        self.state = new_state
        await self.send_json({
            "type": "state_change",
            "from": old.value,
            "to": new_state.value,
        })
        logger.debug(f"[{self.device_id}] State: {old.value} → {new_state.value}")
    
    def update_heartbeat(self):
        self.last_heartbeat = datetime.now()
    
    @property
    def is_idle(self) -> bool:
        return self.state in (DeviceState.IDLE, DeviceState.DND)
    
    @property
    def is_online(self) -> bool:
        return (datetime.now() - self.last_heartbeat).seconds < 60


class SessionManager:
    """全局会话管理器。"""
    
    def __init__(self):
        self.sessions: dict[str, DeviceSession] = {}
        self._global_context: Optional[ConversationContext] = None
    
    def add(self, session: DeviceSession):
        self.sessions[session.device_id] = session
        logger.info(f"[{session.device_id}] Session added ({len(self.sessions)} total)")
    
    def remove(self, device_id: str):
        self.sessions.pop(device_id, None)
        logger.info(f"[{device_id}] Session removed ({len(self.sessions)} remaining)")
    
    def get(self, device_id: str) -> Optional[DeviceSession]:
        return self.sessions.get(device_id)
    
    def get_idle_devices(self) -> list[DeviceSession]:
        return [s for s in self.sessions.values() if s.is_idle and s.is_online]
    
    def get_best_device_for_push(self, target_room: str = "",
                                  exclude: str = "") -> Optional[DeviceSession]:
        """找到最合适的设备推送消息。"""
        best = None
        for s in self.get_idle_devices():
            if s.device_id == exclude:
                continue
            if target_room and s.room != target_room:
                continue
            if s.capabilities.get("screen"):
                return s  # 有屏幕的优先
            if not best or s.device_type == DeviceType.SPEAKER:
                best = s
        return best
    
    def resolve_device_conflict(self, reporting_devices: list[tuple[str, float]]) -> str:
        """多设备同时检测到唤醒词时, 选择最佳设备。
        
        Args:
            reporting_devices: [(device_id, confidence)]
        
        Returns:
            胜出设备的 device_id
        """
        if not reporting_devices:
            return ""
        
        # 按置信度排序, 取最高
        reporting_devices.sort(key=lambda x: x[1], reverse=True)
        return reporting_devices[0][0]
    
    @property
    def online_count(self) -> int:
        return sum(1 for s in self.sessions.values() if s.is_online)
    
    @property
    def global_context(self) -> ConversationContext:
        if self._global_context is None:
            self._global_context = ConversationContext(
                session_id=f"global_{uuid.uuid4().hex[:8]}"
            )
        return self._global_context
