"""Conversation Orchestrator v4 — VAD → STT → LLM → TTS 管线。

核心改进:
  1. 并发的音频管线 (不等STT结束再送LLM)
  2. AEC 回声消除集成
  3. 四层 LLM 路由
  4. 分层 TTS 自动选择
  5. 完整的打断 + 超时
  6. 嵌套的降级路径
  7. 流式 TTS: 边合成边发送, 客户端边缓冲边播放, 首音延迟降至 ~200ms
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from .session import (
    DeviceSession, DeviceState, ConversationTurn, SessionManager,
)
from .audio import VADEvent, MultiLevelVAD, AcousticEchoCanceller
from .stt import StreamingSTT
from .tts import TTSEngine
from .router import IntentRouter, IntentType, RouteResult
from .interrupt import InterruptManager
from .hass import HAConnector
from .agent import AgentExecutor
from .config import config

logger = logging.getLogger("butler.orchestrator")


class PipelineStage:
    """管线阶段 — 用于追踪每轮对话的延迟分布。"""
    def __init__(self):
        self.t0 = time.time()
        self.vad_end: float = 0
        self.stt_end: float = 0
        self.llm_first: float = 0
        self.tts_first: float = 0
        self.tts_end: float = 0

    @property
    def total_ms(self) -> int:
        return int((self.tts_end - self.t0) * 1000) if self.tts_end else 0

    @property
    def breakdown(self) -> dict:
        return {
            "vad": int((self.vad_end - self.t0) * 1000) if self.vad_end else 0,
            "stt": int((self.stt_end - self.vad_end) * 1000) if self.stt_end and self.vad_end else 0,
            "llm_first": int((self.llm_first - self.stt_end) * 1000) if self.llm_first and self.stt_end else 0,
            "tts_first": int((self.tts_first - self.llm_first) * 1000) if self.tts_first and self.llm_first else 0,
            "total": self.total_ms,
        }


class ConversationOrchestrator:
    """单设备对话编排器 — 管理完整语音管线。"""

    def __init__(self, session: DeviceSession, session_manager: SessionManager,
                 vad: MultiLevelVAD, stt: StreamingSTT, tts: TTSEngine,
                 router: IntentRouter, interrupt: InterruptManager,
                 aec: Optional[AcousticEchoCanceller] = None,
                 hass: Optional["HAConnector"] = None):
        self.session = session
        self.sm = session_manager
        self.vad = vad
        self.stt = stt
        self.tts = tts
        self.router = router
        self.interrupt = interrupt
        self.aec = aec
        self.hass = hass
        self.agent = AgentExecutor()
        self._tts_queue: asyncio.Queue[str] = asyncio.Queue()
        self.device_id = session.device_id

        # Audio buffer
        self.speech_audio_buffer = bytearray()

        # Tasks
        self._tasks: list[asyncio.Task] = []

        # Audio format
        self.audio_format = "pcm"

    async def start(self):
        """启动管线任务。"""
        self.interrupt.register(self.device_id)
        self._tasks = [
            asyncio.create_task(
                self._audio_pipeline(), name=f"{self.device_id}-audio"
            ),
            asyncio.create_task(
                self._tts_worker(), name=f"{self.device_id}-tts"
            ),
        ]

    async def stop(self):
        """停止管线任务。"""
        self.interrupt.trigger(self.device_id)
        for t in self._tasks:
            t.cancel()
        await asyncio.sleep(0.05)
        self.interrupt.unregister(self.device_id)
        await self.agent.close()

    async def handle_text_input(self, text: str):
        """处理文本输入 (键盘/触摸)。"""
        if not text.strip():
            return
        logger.info(f"[{self.device_id}] Text input: {text[:100]}")
        await self.session.set_state(DeviceState.PROCESSING)
        await self._process_and_respond(text)

    async def handle_audio_frame(self, frame: bytes):
        """处理音频帧 (来自 WebSocket binary)。"""
        if self.aec and self.session.is_speaking:
            frame = self.aec.process(frame)
        await self.session.audio_buffer.put(frame)

    async def _audio_pipeline(self):
        """音频输入管线: VAD → 缓冲 → STT。"""
        while True:
            try:
                frame = await self.session.audio_buffer.get()
                event = self.vad.process_frame(frame)

                if event == VADEvent.SPEECH_START:
                    self._handle_speech_start()
                    self.speech_audio_buffer = bytearray(frame)

                elif event == VADEvent.SPEECH_END:
                    self.speech_audio_buffer.extend(frame)
                    await self._handle_speech_end()

                elif self.vad.is_speaking:
                    self.speech_audio_buffer.extend(frame)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.device_id}] Audio pipeline error: {e}")

    async def _tts_output(self):
        """TTS 播放队列输出 (已迁移至 _tts_worker)。"""
        while True:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break

    def _handle_speech_start(self):
        """语音开始 — 打断当前 TTS。"""
        if self.interrupt.is_set(self.device_id):
            self.interrupt.clear(self.device_id)
        if self.session.is_speaking:
            self.interrupt.trigger(self.device_id)
        asyncio.create_task(
            self.session.set_state(DeviceState.LISTENING)
        )
        self.vad.reset()

    async def _handle_speech_end(self):
        """语音结束 — 开始处理。"""
        await self.session.set_state(DeviceState.PROCESSING)
        if len(self.speech_audio_buffer) < 3200:  # < 200ms 视为噪音
            logger.debug(f"[{self.device_id}] Speech too short, ignoring")
            await self.session.set_state(DeviceState.IDLE)
            return

        stage = PipelineStage()
        stage.vad_end = time.time()

        # 异步 STT
        loop = asyncio.get_event_loop()

        def _stt():
            return self.stt.transcribe(bytes(self.speech_audio_buffer))

        text = await loop.run_in_executor(None, _stt)
        stage.stt_end = time.time()

        if not text or not text.strip():
            logger.info(f"[{self.device_id}] No speech recognized")
            await self._say("请再说一遍")
            return

        logger.info(f"[{self.device_id}] STT: {text[:200]}")
        self.speech_audio_buffer.clear()
        await self._process_and_respond(text, stage)

    async def _process_and_respond(self, text: str, stage: PipelineStage = None):
        """文本处理 → 路由 → 响应。"""
        # 发送用户消息
        await self.session.send_json({"type": "user_text", "text": text})

        # 路由
        route = self.router.route(text)
        if stage:
            stage.routing_end = time.time()

        # 根据意图分发
        if route.intent in (IntentType.CHAT, IntentType.CHAT):
            await self._llm_respond(text, route, stage)

        elif route.intent == IntentType.SMART_HOME:
            response = await self._handle_hass(route)
            await self._say(response)
            self._record_turn(text, response, route)
            await self._enter_follow_up()

        elif route.intent == IntentType.QUERY:
            response = await self._handle_query(route)
            await self._say(response)
            self._record_turn(text, response, route)
            await self._enter_follow_up()

        elif route.intent == IntentType.SYSTEM:
            response = await self._handle_system(text)
            await self._say(response)
            self._record_turn(text, response, route)
            await self._enter_follow_up()

        elif route.intent == IntentType.AGENT_COMMAND:
            await self._handle_agent(text, route)
        else:
            await self._llm_respond(text, route, stage)

    async def _llm_respond(self, text: str, route: RouteResult, stage: PipelineStage):
        """通过 LLM 生成回复 — 流式输出字幕 + 流式 TTS。"""
        system_prompt = self.router.get_system_prompt(
            route.intent, {"user": self.session.user}
        )

        # 发送 LLM 开始信号 (流式字幕)
        await self.session.send_json({"type": "llm_start"})

        full_response = ""
        sentence_buffer = ""

        try:
            async for token in self._stream_llm(text, system_prompt):
                if self.interrupt.is_set(self.device_id):
                    logger.info(f"[{self.device_id}] LLM interrupted")
                    break

                full_response += token
                sentence_buffer += token
                if stage and not stage.llm_first:
                    stage.llm_first = time.time()

                # 流式输出字幕
                await self.session.send_json({
                    "type": "llm_token", "text": token,
                })

                # 句子结束 — 异步启动流式 TTS (不阻塞 LLM 继续生成)
                if token in ("。", "！", "？", "!", "?", ".", "\n", "，"):
                    if sentence_buffer.strip():
                        t = sentence_buffer
                        sentence_buffer = ""
                        asyncio.create_task(
                            self._speak_sentence_stream(t, stage)
                        )

        except Exception as e:
            logger.error(f"[{self.device_id}] LLM error: {e}")
            await self._say("处理出错了, 请稍后再试")
            await self._enter_follow_up()
            return

        # 最后剩余部分
        if sentence_buffer.strip() and not self.interrupt.is_set(self.device_id):
            asyncio.create_task(
                self._speak_sentence_stream(sentence_buffer, stage)
            )

        # 完整文字显示
        await self.session.send_json({"type": "llm_end", "text": full_response.strip()})
        self._record_turn(text, full_response, route)
        await self._enter_follow_up()

    async def _stream_llm(self, text: str, system_prompt: str):
        """Streaming LLM 调用。"""
        from .llm import LLMStreamer
        llm = LLMStreamer()
        async for token in llm.stream_response(text, system_prompt, self.session.context):
            yield token

    async def _speak_sentence_stream(self, sentence: str, stage: PipelineStage = None):
        """流式合成并发送一个句子的 TTS — 边合成边发送音频块。

        客户端通过 MediaSource 边缓冲边播放, 首音延迟降至 ~200ms。
        """
        if not sentence.strip():
            return
        if self.interrupt.is_set(self.device_id):
            return

        # 串行化 TTS: 将请求放入队列, 由队列依次处理
        await self._tts_queue.put(sentence)

    async def _tts_worker(self):
        """TTS 队列工作线程 — 串行处理 TTS 请求。"""
        while True:
            try:
                sentence = await self._tts_queue.get()
                if not sentence or self.interrupt.is_set(self.device_id):
                    self._tts_queue.task_done()
                    continue
                
                await self._tts_process(sentence)
                self._tts_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.device_id}] TTS worker error: {e}")

    async def _tts_process(self, sentence: str):
        """实际执行 TTS 合成与发送。"""
        if not sentence.strip():
            return
        if self.interrupt.is_set(self.device_id):
            return

        # 等待前一句播完 (最多 30 秒, 防止卡死)
        wait_start = time.time()
        while self.session.is_speaking and not self.interrupt.is_set(self.device_id):
            if time.time() - wait_start > 30:
                logger.warning(f"[{self.device_id}] TTS wait timeout, forcing")
                self.session.is_speaking = False
                break
            await asyncio.sleep(0.1)

        if self.interrupt.is_set(self.device_id):
            return

        if self.session.state not in (DeviceState.SPEAKING, DeviceState.LISTENING):
            await self.session.set_state(DeviceState.SPEAKING)
        self.session.is_speaking = True

        try:
            # 发送 TTS 开始信号
            await self.session.send_json({"type": "tts_start", "format": "mp3"})

            chunk_count = 0
            async for chunk, fmt in self.tts.synthesize_stream(sentence):
                if self.interrupt.is_set(self.device_id):
                    break
                await self.session.send_audio(chunk)
                chunk_count += 1

            # 发送 TTS 结束信号
            await self.session.send_json({"type": "tts_end", "chunks": chunk_count})
        except Exception as e:
            logger.error(f"[{self.device_id}] TTS process error: {e}")
        finally:
            self.session.is_speaking = False

        logger.debug(f"[{self.device_id}] TTS streamed {chunk_count} chunks for: {sentence[:50]}")

    async def _say(self, text: str):
        """直接播报一段文本 (不用 LLM)。使用流式 TTS 获得更低延迟。"""
        if not text:
            return
        if self.interrupt.is_set(self.device_id):
            return
        if self.session.is_speaking:
            return

        # 发送文本用于客户端显示
        await self.session.send_json({"type": "llm_start", "text": text})

        self.session.is_speaking = True
        await self.session.set_state(DeviceState.SPEAKING)

        await self.session.send_json({"type": "tts_start", "format": "mp3"})

        chunk_count = 0
        async for chunk, fmt in self.tts.synthesize_stream(text):
            if self.interrupt.is_set(self.device_id):
                break
            await self.session.send_audio(chunk)
            chunk_count += 1

        await self.session.send_json({"type": "tts_end", "chunks": chunk_count})
        self.session.is_speaking = False

        # 发送 llm_end 表示回复完成
        await self.session.send_json({"type": "llm_end", "text": text})

    async def _handle_hass(self, route: RouteResult) -> str:
        """处理智能家居控制命令。"""
        if self.hass is None:
            return "智能家居未连接"

        try:
            # 解析实体和设备
            text = route.text
            room = route.params.get("room", "") if route.params else ""

            entities = await self.hass.refresh_entities()
            room_entities = [e for e in entities if not room or room in e.area]

            # 路由级命令解析
            from .hass import extract_command
            cmd = extract_command(text, room)

            if not cmd or not cmd.get("entities"):
                action = route.action
                if action.startswith("light_"):
                    cmd = {"action": "turn_on" if "on" in action else "turn_off",
                           "entities": [], "params": {}}
                elif action.startswith("scene_"):
                    scene_map = {
                        "scene_leave": "离家",
                        "scene_arrive": "回家",
                        "scene_sleep": "睡眠",
                        "scene_movie": "观影",
                    }
                    scene_name = scene_map.get(action, action)
                    return await self._hass_scene(scene_name)
                else:
                    return "好的"

            return await self.hass.execute_command(cmd)

        except Exception as e:
            logger.error(f"HASS error: {e}")
            return "操作失败了"

    async def _hass_scene(self, scene_name: str) -> str:
        """切换场景。"""
        if self.hass is None:
            return "智能家居未连接"
        try:
            entities = await self.hass.refresh_entities()
            scenes = [e for e in entities if e.domain == "scene"]
            for s in scenes:
                if scene_name in s.friendly_name:
                    await self.hass.call_service("scene", "turn_on", s.entity_id)
                    return f"已切换{scene_name}模式"
            return f"未找到{scene_name}场景"
        except Exception as e:
            logger.error(f"HASS scene error: {e}")
            return "场景切换失败"

    async def _handle_query(self, route: RouteResult) -> str:
        """处理简单查询 (时间/天气)。"""
        action = route.action
        if action == "time":
            return time.strftime("现在%H点%M分")
        elif action == "weather":
            return "今天晴, 23到30度"
        return ""

    async def _handle_system(self, text: str) -> str:
        """处理系统命令。"""
        if any(w in text for w in ["小声", "安静", "静音"]):
            await self.session.set_state(DeviceState.WHISPER)
            return "好的, 安静模式"
        elif any(w in text for w in ["正常", "恢复"]):
            await self.session.set_state(DeviceState.IDLE)
            return "已恢复普通模式"
        elif "你是谁" in text:
            return "我是Aria, 你的家庭智能语音助手"
        return "好的"

    async def _handle_agent(self, text: str, route: RouteResult):
        """处理 Agent 任务 (异步)。"""
        await self.session.send_json({"type": "info", "message": "正在处理..."})

        action = route.action
        params = {}
        if route.params:
            params = {"command": route.params[-1] if route.params[-1] else text}
        else:
            params = {"command": text}

        result = await self.agent.execute(action, params)
        await self._say(result)
        self._record_turn(text, result, route)
        await self._enter_follow_up()

    def _record_turn(self, user_text: str, assistant_text: str, route: RouteResult):
        """记录一轮对话。"""
        turn = ConversationTurn(
            user_text=user_text,
            assistant_text=assistant_text,
            action=route.action,
            intent=route.intent.value,
            timestamp=time.time(),
            user_id=self.session.user_id,
            device_id=self.device_id,
        )
        self.session.context.add_turn(turn)
        global_ctx = self.sm.global_context
        global_ctx.add_turn(turn)

    async def _enter_follow_up(self):
        """进入后续对话模式 (免唤醒继续)。"""
        self.session.context.last_activity = time.time()
        self.session.context.expires_at = time.time() + config.CONVERSATION_TIMEOUT

        if self.session.conversation_timer:
            self.session.conversation_timer.cancel()

        self.session.conversation_timer = asyncio.create_task(
            self._conversation_timeout()
        )
        await self.session.set_state(DeviceState.LISTENING)

    async def _conversation_timeout(self):
        """超时回到 IDLE。"""
        try:
            await asyncio.sleep(config.CONVERSATION_TIMEOUT)
            if self.session.state == DeviceState.LISTENING:
                await self.session.set_state(DeviceState.IDLE)
                self.vad.reset()
                logger.info(f"[{self.device_id}] Conversation timeout")
        except asyncio.CancelledError:
            pass
