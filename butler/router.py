"""Intent Router — 从规则匹配到 LLM 的四层路由。

Layer 0: 规则匹配 (零模型, < 1ms)
Layer 1: 轻量分类 (关键词 + BERT — 可选)
Layer 2: LLM 流式生成 (DeepSeek API / 本地)
Layer 3: Agent 平台调度
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger("butler.router")


class IntentType(Enum):
    SMART_HOME = "smart_home"
    QUERY = "query"
    AGENT_COMMAND = "agent_command"
    CHAT = "chat"
    SYSTEM = "system"
    UNKNOWN = "unknown"


@dataclass
class RouteResult:
    intent: IntentType
    action: str = ""
    params: tuple = ()
    confidence: float = 0.0
    text: str = ""
    handler: Optional[Callable] = None


class RuleRouter:
    """Layer 0: 规则匹配路由器。
    
    预编译正则匹配, 零模型延迟 (< 1ms)。
    覆盖 ~40% 的日常命令。
    """
    
    def __init__(self):
        self.patterns: list[tuple[re.Pattern, IntentType, str]] = [
            # === 灯光 ===
            (re.compile(r"^(打开|开|开启)\s*(.*?)(灯|灯光|照明)"),
             IntentType.SMART_HOME, "light_on"),
            (re.compile(r"^(关|关闭|关了)\s*(.*?)(灯|灯光|照明)"),
             IntentType.SMART_HOME, "light_off"),
            (re.compile(r"^开灯$"),
             IntentType.SMART_HOME, "light_on"),
            (re.compile(r"^关灯$"),
             IntentType.SMART_HOME, "light_off"),
            (re.compile(r"(关闭|关|关了|关上)\s*(.*?)(灯|灯光|照明)"),
             IntentType.SMART_HOME, "light_off"),
            (re.compile(r"把\s*(.+?)(灯|灯光)\s*(调亮|调暗|调到|设成)\s*(\d+)%?"),
             IntentType.SMART_HOME, "light_brightness"),
            
            # === 空调 ===
            (re.compile(r"(打开|开|开启)\s*(.+?)空调"),
             IntentType.SMART_HOME, "ac_on"),
            (re.compile(r"(关闭|关|关了)\s*(.+?)空调"),
             IntentType.SMART_HOME, "ac_off"),
            (re.compile(r"把\s*(.+?)空调\s*(调到|设为|设定|设成)\s*(\d+)度"),
             IntentType.SMART_HOME, "ac_temp"),
            (re.compile(r"空调\s*(\d+)度"),
             IntentType.SMART_HOME, "ac_temp"),
            (re.compile(r"(有点|太)?(冷|热)"),
             IntentType.SMART_HOME, "ac_feel"),
            
            # === 窗帘 ===
            (re.compile(r"(打开|拉开|开)\s*(.+?)(窗帘|帘子|纱)"),
             IntentType.SMART_HOME, "curtain_open"),
            (re.compile(r"(关闭|拉上|拉|关上|关了)\s*(.+?)(窗帘|帘子|纱)"),
             IntentType.SMART_HOME, "curtain_close"),
            
            # === 场景 ===
            (re.compile(r"(我)?(要)?(出门了|离家|出去了|去上班)"),
             IntentType.SMART_HOME, "scene_leave"),
            (re.compile(r"(我)?(回[来家]了|到家|回来了)"),
             IntentType.SMART_HOME, "scene_arrive"),
            (re.compile(r"(我要)?(睡觉了|晚安|休息)"),
             IntentType.SMART_HOME, "scene_sleep"),
            (re.compile(r"(我要)?(看电影|看片|影院|投影)"),
             IntentType.SMART_HOME, "scene_movie"),
            
            # === 查询 ===
            (re.compile(r"(今天|明天|后天)\s*(天气|温度|下雨|下雪|刮风|晴|阴)"),
             IntentType.QUERY, "weather"),
            (re.compile(r"(现在|当前)\s*(几点了|什么时间|几点)"),
             IntentType.QUERY, "time"),
            (re.compile(r"(天气|气温).*(怎么样|如何|多少)"),
             IntentType.QUERY, "weather"),
            (re.compile(r"^(几点了|现在几点|时间)$"),
             IntentType.QUERY, "time"),
            
            # === Agent 命令 ===
            (re.compile(r"(检查|查看|巡检)(服务器|系统|状态|机器|节点)"),
             IntentType.AGENT_COMMAND, "inspect"),
            (re.compile(r"(执行|运行|调度|跑)\s*(.*?)(任务|脚本)"),
             IntentType.AGENT_COMMAND, "run_task"),
            
            # === 系统 ===
            (re.compile(r"(小声|安静|静音|别说话|闭嘴)"),
             IntentType.SYSTEM, "silent_mode"),
            (re.compile(r"(正常|恢复|大声)"),
             IntentType.SYSTEM, "normal_mode"),
            (re.compile(r"(你是谁|你叫什么|你名字|谁是你)"),
             IntentType.SYSTEM, "who_are_you"),
            (re.compile(r"(切换|进入).*?(模式)"),
             IntentType.SYSTEM, "switch_mode"),
        ]
    
    def route(self, text: str) -> Optional[RouteResult]:
        """路由匹配。匹配成功返回 RouteResult, 否则 None。"""
        cleaned = text.strip().lower()
        
        for pattern, intent, action in self.patterns:
            match = pattern.search(cleaned)
            if match:
                return RouteResult(
                    intent=intent,
                    action=action,
                    params=match.groups(),
                    confidence=0.9,
                    text=text,
                )
        
        return None


class IntentRouter:
    """四层意图路由器。
    
    Layer 0: 规则匹配 (零模型)
    Layer 1: 关键词辅助分类
    Layer 2: LLM 路由 (用于复杂/模糊请求)
    """
    
    def __init__(self):
        self.rule_router = RuleRouter()
        
        # Layer 1: 关键词辅助分类 (当规则匹配不到时)
        self._keyword_map = {
            IntentType.SMART_HOME: ["灯", "空调", "窗帘", "电视", "风扇",
                                     "开关", "调", "温度", "亮度", "场景",
                                     "打开", "关闭", "开", "关"],
            IntentType.QUERY: ["天气", "温度", "时间", "日期", "星期",
                               "新闻", "股票", "汇率", "价格", "多少"],
            IntentType.AGENT_COMMAND: ["检查", "执行", "运行", "调度",
                                       "服务器", "任务", "脚本", "部署"],
            IntentType.SYSTEM: ["模式", "设置", "配置", "音量", "安静",
                                "你是谁", "小声", "静音"],
        }
        
        logger.info("Router initialized")
    
    def route(self, text: str) -> RouteResult:
        """路由入口。
        
        Args:
            text: 用户输入文本
            
        Returns:
            RouteResult 包含意图和动作
        """
        # Layer 0: 规则匹配 (最快路径)
        result = self.rule_router.route(text)
        if result:
            return result
        
        # Layer 1: 关键词辅助分类
        intent = self._keyword_classify(text)
        
        return RouteResult(
            intent=intent,
            action="",
            confidence=0.5,
            text=text,
        )
    
    def _keyword_classify(self, text: str) -> IntentType:
        """关键词分类 — 当规则匹配不到时使用。"""
        text_lower = text.lower()
        scores = {}
        
        for intent, keywords in self._keyword_map.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > 0:
                scores[intent] = score
        
        if scores:
            return max(scores, key=scores.get)
        
        return IntentType.CHAT
    
    def get_system_prompt(self, intent: IntentType, context: dict = None) -> str:
        """根据意图返回对应的 system prompt。"""
        prompts = {
            IntentType.CHAT: (
                "你是Aria, 一个智能家庭语音助手。\n"
                "规则:\n"
                "- 回复口语化, 简短自然, 像真人说话\n"
                "- 不要列点, 不用markdown, 不用特殊符号\n"
                "- 确认操作要简短: \"好的\" \"已经打开了\"\n"
                "- 查询要直接给答案: \"今天25到30度, 晴\"\n"
                "- 不要说\"我帮你查一下\"这种废话\n"
                "- 多轮对话中记住上下文\n"
                "- 没听懂说\"能再说一遍吗\"\n"
            ),
            IntentType.QUERY: (
                "你是Aria。用户查询信息。\n"
                "直接给答案, 不要客套。\n"
                "如果不知道就说\"这个我还不清楚\"。\n"
            ),
            IntentType.SMART_HOME: (
                "你是Aria。用户要做智能家居操作。\n"
                "简短确认: \"好的\" \"已经打开了\" \"设置好了\"。\n"
                "如果操作失败, 说\"操作失败了, 请检查设备\"。\n"
            ),
            IntentType.AGENT_COMMAND: (
                "你是Aria。用户要通过Agent平台执行任务。\n"
                "先确认任务内容, 然后调Agent平台执行。\n"
                "完成后汇报结果摘要。\n"
            ),
            IntentType.SYSTEM: (
                "你是Aria。用户要调整系统设置。\n"
                "简短回应并按指示执行。\n"
            ),
        }
        return prompts.get(intent, prompts[IntentType.CHAT])
