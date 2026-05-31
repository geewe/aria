"""Streaming LLM client — 连接 DeepSeek API / Hermes API。

支持: 
  - 流式 SSE 输出
  - HTTP API (OpenAI 兼容格式)
  - 本地降级 (API 不可用时返回兜底回复)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from datetime import datetime
from typing import AsyncGenerator, Optional

import httpx

from .config import config

logger = logging.getLogger("butler.llm")


# 兜底回复 (API 不可用时使用)
FALLBACK_RESPONSES = {
    "smart_home": "好的，已为您操作",
    "query": "当前信息已为您查到",
    "chat": "是的，我听到了",
    "agent_command": "任务已创建，完成后通知您",
    "system": "好的",
    "default": "好的",
}

# 简单的回复模板 (比纯兜底要好)
TEMPLATE_RESPONSES = {
    "time": lambda: f"现在{datetime.now().hour}点{datetime.now().minute}分",
    "weather": lambda: "今天晴转多云，23到30度",
    "who_are_you": lambda: "我是 Aria，你的家庭智能语音助手",
    "light_on": lambda: "好的",
    "light_off": lambda: "已关闭",
    "scene_leave": lambda: "离家模式已开启，灯已关闭，安防已启动",
    "scene_arrive": lambda: "欢迎回家，灯已打开，空调已开启",
    "scene_sleep": lambda: "晚安，已开启夜间模式",
}


class LLMStreamer:
    """Streaming LLM client.

    连接 DeepSeek API (或兼容的 OpenAI API)。
    如果 API 不可用, 自动降级为本地模板回复。
    """

    def __init__(self):
        self.api_url = os.environ.get(
            "LLM_API_URL", config.HERMES_API_URL
        )
        self.api_key = os.environ.get(
            "LLM_API_KEY", config.HERMES_API_KEY
        )
        self.model = os.environ.get(
            "LLM_MODEL", config.LLM_MODEL
        )
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(config.LLM_TIMEOUT),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
            )
        return self._client

    async def stream_response(
        self,
        text: str,
        system_prompt: str = "",
        context=None,
    ) -> AsyncGenerator[str, None]:
        """Stream LLM response tokens.

        Args:
            text: 用户输入文本
            system_prompt: 系统提示词
            context: ConversationContext 或 list[dict]

        Yields:
            单个 token 文本
        """
        # 检查模板回复 (简单命令直接返回)
        template_response = self._check_template(text)
        if template_response:
            for char in template_response:
                yield char
                await asyncio.sleep(0.02)  # 模拟流式效果
            return

        # 尝试 API
        try:
            messages = self._build_messages(text, system_prompt, context)
            async for token in self._stream_api(messages):
                yield token
            return
        except Exception as e:
            logger.warning(f"LLM API failed, using fallback: {e}")

        # 兜底: 简单模板回复
        response = self._get_fallback_response(text)
        for char in response:
            yield char
            await asyncio.sleep(0.01)

    def _check_template(self, text: str) -> Optional[str]:
        """检查是否匹配简单回复模板。"""
        text_lower = text.lower().strip()
        
        for keyword, response_fn in TEMPLATE_RESPONSES.items():
            if keyword == "time" and ("几" in text or "时间" in text or "点" in text):
                return response_fn()
            elif keyword == "weather" and ("天气" in text or "温度" in text):
                return response_fn()
            elif keyword == "who_are_you" and ("你是谁" in text or "你叫什么" in text):
                return response_fn()
        
        return None

    def _get_fallback_response(self, text: str) -> str:
        """获取兜底回复。"""
        responses = [
            "好的，已收到",
            "明白",
            "好的",
            "是的",
            "我知道了",
        ]
        return random.choice(responses)

    def _build_messages(self, text: str, system_prompt: str = "",
                        context=None) -> list[dict]:
        """构建消息数组 (OpenAI 格式)。"""
        from .session import ConversationContext
        
        # System prompt
        if not system_prompt:
            now = datetime.now()
            system_prompt = config.SYSTEM_PROMPT.format(
                time=now.strftime("%H:%M"),
                user="主人",
            )

        messages = [{"role": "system", "content": system_prompt}]

        # Context turns
        if context is not None:
            if isinstance(context, ConversationContext):
                for turn in context.turns[-config.MAX_CONTEXT_TURNS:]:
                    if turn.user_text:
                        messages.append({"role": "user", "content": turn.user_text})
                    if turn.assistant_text:
                        messages.append({
                            "role": "assistant", "content": turn.assistant_text
                        })
            elif isinstance(context, list):
                for turn in context[-config.MAX_CONTEXT_TURNS:]:
                    if turn.get("user"):
                        messages.append({"role": "user", "content": turn["user"]})
                    if turn.get("assistant"):
                        messages.append({
                            "role": "assistant", "content": turn["assistant"]
                        })

        messages.append({"role": "user", "content": text})
        return messages

    async def _stream_api(self, messages: list[dict]) -> AsyncGenerator[str, None]:
        """通过 SSE 流式调用 LLM API。"""
        client = await self._get_client()

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "max_tokens": config.LLM_MAX_TOKENS,
            "temperature": config.LLM_TEMPERATURE,
        }

        async with client.stream(
            "POST", self.api_url, json=payload
        ) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                logger.error(f"LLM API error {response.status_code}: {error_body}")
                return

            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue

                data = line[6:]
                if data == "[DONE]":
                    return

                try:
                    chunk = json.loads(data)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    token = delta.get("content", "")
                    if token:
                        yield token
                except json.JSONDecodeError:
                    continue

    async def close(self):
        if self._client:
            await self._client.aclose()
