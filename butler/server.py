"""Aria 家庭助手 v4 — 主 WebSocket 服务器.

全双工语音管线:
  设备 → WebSocket → VAD → AEC → STT → LLM → TTS → 设备
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import config
from .session import DeviceSession, DeviceState, SessionManager
from .audio import MultiLevelVAD, AcousticEchoCanceller
from .stt import StreamingSTT
from .tts import TTSEngine
from .router import IntentRouter
from .hass import HAConnector
from .interrupt import InterruptManager
from .orchestrator import ConversationOrchestrator
from .security import DeviceAuth, RateLimiter, AuditLogger, Permission
from .monitor import MetricsCollector, AlertManager, TraceSpan, metrics

logger = logging.getLogger("butler.server")


class ButlerServer:
    """v4 Butler 服务器 — 全双工语音管线。"""

    def __init__(self):
        # Core
        self.sm = SessionManager()
        self.interrupt = InterruptManager()
        
        # Audio pipeline
        self.vad = MultiLevelVAD()
        self.aec = AcousticEchoCanceller()
        self.stt = StreamingSTT()
        self.tts = TTSEngine()
        self.router = IntentRouter()
        
        # Security
        self.auth = DeviceAuth(str(config.DATA_DIR))
        self.rate_limiter = RateLimiter()
        self.audit = AuditLogger(str(config.DATA_DIR / "audit.db"))
        
        # HomeAssistant
        self.hass = HAConnector(config.HASS_URL, config.HASS_TOKEN)
        
        # Per-device orchestrators
        self.orchestrators: dict[str, ConversationOrchestrator] = {}
        
        logger.info("=" * 50)
        logger.info("Aria 家庭助手 v4 initialized")
        logger.info(f"  VAD:  MultiLevel (energy + spectral)")
        logger.info(f"  AEC:  NLMS ({self.aec.filter_length}taps)")
        logger.info(f"  STT:  {self._fmt_stt()}")
        logger.info(f"  TTS:  {self._fmt_tts()}")
        logger.info(f"  Auth: {'enabled' if self.auth else 'disabled'}")
        logger.info("=" * 50)
    
    def _fmt_stt(self) -> str:
        engines = []
        if self.stt.sense_voice.is_available():
            engines.append("SenseVoice")
        if self.stt.fast_whisper.is_available():
            engines.append("Whisper")
        engines.append("EdgeAPI")
        return " → ".join(engines)
    
    def _fmt_tts(self) -> str:
        return "Cache → EdgeTTS → macOS say"

    async def handle_websocket(self, ws: WebSocket):
        """处理设备 WebSocket 连接。"""
        try:
            await ws.accept()
        except Exception as e:
            logger.error(f"WebSocket accept failed: {e}")
            return

        # 设备注册
        device_id = f"dev_{uuid.uuid4().hex[:8]}"
        session = DeviceSession(ws, device_id)
        self.sm.add(session)
        self.interrupt.register(device_id)

        # 创建编排器
        orchestrator = ConversationOrchestrator(
            session, self.sm, self.vad, self.stt, self.tts,
            self.router, self.interrupt, self.aec, self.hass,
        )
        self.orchestrators[device_id] = orchestrator
        
        self.vad.reset()

        try:
            # 发送欢迎
            await session.send_json({
                "type": "connected",
                "device_id": device_id,
                "version": "4.1.0",
                "server": "Aria 家庭助手 v4",
            })

            # 启动管线
            await orchestrator.start()

            # 主消息循环
            while True:
                # 接收文本或二进制消息
                raw = await ws.receive()
                if raw["type"] == "websocket.disconnect":
                    break
                
                if "bytes" in raw:
                    # 音频帧
                    await orchestrator.handle_audio_frame(raw["bytes"])
                elif "text" in raw:
                    try:
                        message = json.loads(raw["text"])
                    except json.JSONDecodeError:
                        continue
                    
                    if not self.rate_limiter.check(device_id):
                        await session.send_json({
                            "type": "error", "code": "rate_limited",
                            "message": "请求太频繁",
                        })
                        continue
                    await self._dispatch_message(session, message, orchestrator)

        except WebSocketDisconnect:
            logger.info(f"[{device_id}] Disconnected")
        except Exception as e:
            logger.error(f"[{device_id}] Error: {e}")
        finally:
            await self._cleanup(device_id, orchestrator)

    async def _dispatch_message(self, session: DeviceSession,
                                 data: dict, orch: ConversationOrchestrator):
        """分发 WebSocket 消息到对应处理器。"""
        msg_type = data.get("type", "")
        device_id = session.device_id

        if msg_type == "ping":
            session.update_heartbeat()
            await session.send_json({"type": "pong"})

        elif msg_type == "device_info":
            session.device_type = data.get("device_type", "unknown")
            session.room = data.get("room", "unknown")
            session.user = data.get("user", "default")
            session.capabilities.update(data.get("capabilities", {}))
            logger.info(f"[{device_id}] Registered: {session.device_type} @ {session.room}")

        elif msg_type == "text":
            # 文本输入
            await orch.handle_text_input(data.get("text", ""))

        elif msg_type == "vad":
            # 客户端 VAD 事件 (可选)
            state = data.get("state", "")
            if state == "speech_start":
                orch._handle_speech_start()
            elif state == "speech_end":
                # 客户端VAD结束 → 服务器用缓冲区中的音频做STT
                await orch._handle_speech_end()

        elif msg_type == "interrupt":
            # 用户主动打断
            self.interrupt.trigger(device_id)

        elif msg_type == "set_mode":
            mode = data.get("mode", "normal")
            mode_map = {
                "normal": DeviceState.IDLE,
                "whisper": DeviceState.WHISPER,
                "silent": DeviceState.SILENT,
                "dnd": DeviceState.DND,
            }
            if mode in mode_map:
                await session.set_state(mode_map[mode])

    async def _cleanup(self, device_id: str, orch: ConversationOrchestrator):
        """清理设备资源。"""
        await orch.stop()
        self.interrupt.unregister(device_id)
        self.orchestrators.pop(device_id, None)
        self.sm.remove(device_id)
        logger.info(f"[{device_id}] Cleaned up")

    # === REST API 端点 ==

    async def handle_health(self) -> dict:
        """健康检查端点。"""
        health = metrics.get_health()
        health["devices"] = {
            "total": len(self.sm.sessions),
            "online": self.sm.online_count,
            "list": [
                {
                    "id": s.device_id,
                    "type": s.device_type.value if hasattr(s.device_type, 'value') else str(s.device_type),
                    "room": s.room,
                    "state": s.state.value,
                    "user": s.user,
                }
                for s in self.sm.sessions.values()
            ],
        }
        health["tts_stats"] = self.tts.get_stats()
        health["version"] = "4.1.0"
        return health

    async def handle_metrics(self) -> dict:
        """详细指标端点。"""
        return {
            "health": await self.handle_health(),
            "recent_conversations": metrics.get_recent_conversations(20),
            "tts_stats": self.tts.get_stats(),
        }

    async def handle_push(self, text: str, priority: str = "normal",
                           target: Optional[str] = None) -> bool:
        """主动推送消息到设备。"""
        session = None
        if target:
            session = self.sm.get(target)
        else:
            session = self.sm.get_best_device_for_push()
        
        if session and session.is_idle:
            audio, fmt = await self.tts.synthesize(text)
            if audio:
                await session.send_json({
                    "type": "push", "text": text, "priority": priority,
                })
                await session.send_audio(audio)
                return True
        return False

    async def handle_audio_binary(self, session: DeviceSession,
                                   data: bytes, orch: ConversationOrchestrator):
        """处理二进制音频帧 (16kHz 16bit PCM)。"""
        await orch.handle_audio_frame(data)


# === 全局实例 ===
butler = ButlerServer()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Aria 家庭助手 v4 starting...")
    yield
    logger.info("Aria 家庭助手 v4 shutting down...")


app = FastAPI(
    title="Aria 家庭助手 v4",
    version="4.1.0",
    lifespan=lifespan,
)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket 端点 — 设备全双工连接。"""
    await butler.handle_websocket(ws)


@app.get("/health")
async def health_check():
    """健康检查。"""
    return await butler.handle_health()


@app.get("/metrics")
async def metrics_endpoint():
    """详细指标。"""
    return await butler.handle_metrics()


@app.get("/push")
async def push_get(text: str, priority: str = "normal",
                    target: Optional[str] = None):
    """推送消息 (GET)。"""
    ok = await butler.handle_push(text, priority, target)
    return {"success": ok}


@app.post("/push")
async def push_post(request: Request):
    """推送消息 (POST)。"""
    data = await request.json()
    ok = await butler.handle_push(
        data.get("text", ""), data.get("priority", "normal"),
        data.get("target", None),
    )
    return {"success": ok}


@app.get("/api/hass/status")
async def hass_status():
    """HA 连接状态。"""
    if not butler.hass or not butler.hass.token:
        return {"connected": False, "reason": "未配置 HASS_TOKEN"}
    try:
        entities = await butler.hass.refresh_entities()
        return {
            "connected": True,
            "entity_count": len(entities),
            "url": butler.hass.url,
        }
    except Exception as e:
        return {"connected": False, "reason": str(e)}

@app.get("/api/config")
async def get_config():
    """获取当前配置（API密钥等敏感信息已过滤）。"""
    from .config import config
    return config.to_dict()


@app.post("/api/config/reload")
async def reload_config():
    """热重载配置文件。"""
    from .config import config
    config._load_yaml()
    config._apply_env_overrides()
    return {"status": "ok", "message": "配置已重新加载"}


@app.get("/", include_in_schema=False)
async def root_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/index.html")

# 如果有 static 目录, 挂载
import os
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
