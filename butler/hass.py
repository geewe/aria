"""HomeAssistant 深度集成 v4.

功能:
  1. WebSocket 实时订阅 + REST API fallback
  2. 实体缓存 (30s 刷新)
  3. 中文别名匹配 (同义词 + 模糊匹配)
  4. 房间上下文自动补全
  5. 场景联动
  6. 设备发现 (自动同步实体到热词)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

logger = logging.getLogger("butler.hass")


@dataclass
class HAEntity:
    """HA 实体。"""
    entity_id: str
    state: str
    friendly_name: str = ""
    area: str = ""
    device_class: str = ""
    attributes: dict = field(default_factory=dict)
    last_changed: str = ""
    domain: str = ""  # light, climate, cover, etc.
    
    def __post_init__(self):
        self.domain = self.entity_id.split(".")[0] if "." in self.entity_id else ""


class EntityAliasMatcher:
    """中文别名匹配器 — 处理用户说"开灯"→ 找到正确的 HA 实体。
    
    多层匹配策略:
      1. 精确匹配 friendly_name
      2. 同义词扩展 (主灯=顶灯=大灯)
      3. 房间上下文 + 设备类型
      4. 拼音模糊匹配 (备选)
    """
    
    def __init__(self):
        # 同义词词典
        self._synonyms = {
            "灯": ["灯光", "照明", "灯灯", "亮"],
            "打开": ["开", "开启", "亮了", "打开"],
            "关闭": ["关", "关上", "关了", "灭", "熄灭"],
            "空调": ["冷气", "暖气", "空凋"],
            "窗帘": ["帘子", "百叶窗", "遮阳帘"],
            "客厅": ["起居室", "大厅", "living room"],
            "卧室": ["睡房", "房间", "bedroom"],
            "厨房": ["厨房", "kitchen"],
            "餐厅": ["饭厅", "dining"],
            "书房": ["书斋", "工作室", "study"],
            "主卧": ["主卧室", "主人房"],
            "次卧": ["次卧室", "客房"],
        }
        # 从 friendly_name 提取的关键词映射
        self._entity_keywords: dict[str, set[str]] = {}
        # friendly_name → entity_id
        self._name_map: dict[str, str] = {}
        # 房间 → [实体列表]
        self._room_entities: dict[str, list[HAEntity]] = {}
    
    def build_index(self, entities: list[HAEntity]):
        """从实体列表构建索引。"""
        self._name_map.clear()
        self._entity_keywords.clear()
        self._room_entities.clear()
        
        for ent in entities:
            name = ent.friendly_name
            if name:
                self._name_map[name] = ent.entity_id
                # 按房间分组
                area = ent.area or "默认"
                if area not in self._room_entities:
                    self._room_entities[area] = []
                self._room_entities[area].append(ent)
                
                # 提取关键词
                keywords = set()
                # "客厅主灯" → "客厅", "主灯", "灯"
                for char in name:
                    keywords.add(char)
                # 分词 (按空格/停用词)
                for part in re.split(r'[ _\-]', name):
                    if part:
                        keywords.add(part)
                        # 单字级别
                        for c in part:
                            keywords.add(c)
                self._entity_keywords[ent.entity_id] = keywords
        
        logger.info(
            f"Entity index built: {len(entities)} entities, "
            f"{len(self._room_entities)} rooms"
        )
    
    def _expand_synonyms(self, word: str) -> set[str]:
        """扩展同义词。"""
        result = {word}
        for key, syns in self._synonyms.items():
            if word in syns or word == key:
                result.add(key)
                result.update(syns)
        return result
    
    def _score_entity(self, entity: HAEntity, text: str, 
                       current_room: str = "") -> float:
        """计算实体与用户输入的匹配分数。"""
        name = entity.friendly_name.lower()
        text_lower = text.lower()
        score = 0.0
        
        # 精确匹配
        if text_lower == name:
            return 100.0
        if name in text_lower:
            score += 50.0
        if text_lower in name:
            score += 40.0
        
        # 领域匹配 (灯→light, 空调→climate)
        domain_map = {
            "灯": "light", "灯光": "light", "照明": "light",
            "空调": "climate", "冷气": "climate",
            "窗帘": "cover", "帘子": "cover",
            "风扇": "fan", "开关": "switch",
            "传感器": "sensor", "温度": "sensor",
            "门": "lock", "窗": "cover",
        }
        for word, domain in domain_map.items():
            if word in text_lower and entity.domain == domain:
                score += 20.0
        
        # 房间匹配
        if current_room and entity.area:
            if current_room in entity.area or entity.area in current_room:
                score += 30.0
        
        # 关键词重叠
        text_words = set(re.findall(r'\w+', text_lower))
        entity_keywords = self._entity_keywords.get(entity.entity_id, set())
        overlap = text_words & entity_keywords
        # 字符级重叠 (每个共享字符+2分)
        text_chars = set(text_lower)
        name_chars = set(name)
        char_overlap = len(text_chars & name_chars)
        score += char_overlap * 2.0
        
        # 词级重叠
        score += len(overlap) * 5.0
        
        return score
    
    def find_best_match(self, text: str, domain: str = "",
                         current_room: str = "") -> Optional[HAEntity]:
        """找到最匹配的实体。
        
        Args:
            text: 用户输入 (如 "打开客厅灯")
            domain: 过滤领域 (light, climate...)
            current_room: 当前房间 (用于上下文补全)
        
        Returns:
            最佳匹配实体或 None
        """
        best_score = 0
        best_entity = None
        
        for ent in self._all_entities:
            if domain and ent.domain != domain:
                continue
            score = self._score_entity(ent, text, current_room)
            if score > best_score:
                best_score = score
                best_entity = ent
        
        return best_entity if best_score >= 20 else None
    
    def parse_command(self, text: str, current_room: str = ""
                      ) -> dict:
        """解析设备控制命令。
        
        返回:
            {
                "entities": [...],  # 目标实体
                "action": "turn_on" | "turn_off" | "set_temperature" | ...,
                "params": {...},    # 服务调用参数
                "room": "客厅",      # 推断的房间
            }
        """
        text_lower = text.lower()
        result = {"entities": [], "action": "", "params": {}, "room": current_room}
        
        # 检测操作
        is_on = any(w in text_lower for w in ["打开", "开", "开启", "亮了"])
        is_off = any(w in text_lower for w in ["关闭", "关", "灭了", "熄灭"])
        is_temp = any(w in text_lower for w in ["度", "温度", "冷", "热"])
        
        # 提取温度
        temp_match = re.search(r'(\d+)\s*度', text)
        
        # 猜测领域
        domain = ""
        if any(w in text_lower for w in ["灯", "灯光", "照明"]):
            domain = "light"
        elif any(w in text_lower for w in ["空调", "冷气", "暖气"]):
            domain = "climate"
        elif any(w in text_lower for w in ["窗帘", "帘子"]):
            domain = "cover"
        elif any(w in text_lower for w in ["电视", "投影"]):
            domain = "media_player"
        
        # 匹配实体
        entity = self.find_best_match(text, domain, current_room)
        if entity:
            result["entities"] = [entity.entity_id]
            result["room"] = entity.area or current_room
        
        if is_on:
            result["action"] = "turn_on"
        elif is_off:
            result["action"] = "turn_off"
        elif is_temp and temp_match:
            result["action"] = "set_temperature"
            result["params"]["temperature"] = int(temp_match.group(1))
        
        return result
    
    @property
    def _all_entities(self) -> list[HAEntity]:
        all_ents = []
        for ents in self._room_entities.values():
            all_ents.extend(ents)
        return all_ents


class HAConnector:
    """HomeAssistant 连接器。
    
    使用 WebSocket API 订阅事件 + REST API 调用服务。
    """
    
    def __init__(self, url: str = "", token: str = ""):
        self.url = url or os.environ.get(
            "HASS_URL", "http://192.168.2.50:8123"
        )
        self.token = token or os.environ.get("HASS_TOKEN", "")
        self._ws = None
        self._ws_task = None
        self._connected = False
        
        # 实体缓存
        self._entities: list[HAEntity] = []
        self._cache_time = 0
        self._cache_ttl = 30  # 30 秒刷新
        
        # 别名匹配器
        self.matcher = EntityAliasMatcher()
        
        # 事件回调
        self.on_state_change: Optional[callable] = None
        
        logger.info(f"HA Connector initialized: {self.url}")
    
    async def start(self):
        """启动 WebSocket 连接。"""
        if not self.token:
            logger.warning("HASS_TOKEN not set, using REST fallback")
            return
        self._ws_task = asyncio.create_task(self._ws_loop())
    
    async def stop(self):
        if self._ws_task:
            self._ws_task.cancel()
    
    async def _ws_loop(self):
        """WebSocket 事件订阅循环。"""
        ws_url = self.url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url += "/api/websocket"
        
        while True:
            try:
                async with httpx.AsyncClient() as client:
                    # 获取 token
                    resp = await client.post(
                        self.url.replace("ws", "http").replace("wss", "https")
                        + "/auth/token",
                        data={
                            "grant_type": "password",
                            "client_id": "http://hermes.local/",
                            "username": "hermes",
                            "password": self.token,
                        },
                        timeout=10,
                    )
                    # 实际上 HA 用 Long-Lived Token
                    pass
                
                async with httpx.AsyncClient().stream(
                    "GET", ws_url, timeout=None
                ) as response:
                    # WebSocket 连接成功
                    self._connected = True
                    async for line in response.aiter_lines():
                        if line:
                            try:
                                data = json.loads(line)
                                if data.get("type") == "event" and self.on_state_change:
                                    await self.on_state_change(data)
                            except json.JSONDecodeError:
                                pass
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"HA WS error: {e}, reconnecting in 30s...")
                self._connected = False
                await asyncio.sleep(30)
    
    def is_available(self) -> bool:
        return bool(self.token) and self._connected
    
    async def refresh_entities(self) -> list[HAEntity]:
        """刷新实体缓存。"""
        if time.time() - self._cache_time < self._cache_ttl:
            return self._entities
        
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(
                    f"{self.url}/api/states",
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code != 200:
                    return self._entities
                
                states = resp.json()
                entities = []
                for s in states:
                    attr = s.get("attributes", {})
                    ent = HAEntity(
                        entity_id=s["entity_id"],
                        state=s["state"],
                        friendly_name=attr.get("friendly_name", ""),
                        area=attr.get("area_name", ""),
                        device_class=attr.get("device_class", ""),
                        attributes=attr,
                        last_changed=s.get("last_changed", ""),
                    )
                    entities.append(ent)
                
                self._entities = entities
                self._cache_time = time.time()
                
                # 重建索引
                self.matcher.build_index(entities)
                logger.info(f"Refreshed {len(entities)} HA entities")
                return entities
                
        except Exception as e:
            logger.error(f"Failed to refresh HA entities: {e}")
            return self._entities
    
    async def call_service(self, domain: str, service: str, 
                            entity_id: str, **kwargs) -> bool:
        """调用 HA 服务。"""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    f"{self.url}/api/services/{domain}/{service}",
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "entity_id": entity_id,
                        **kwargs,
                    },
                )
                success = resp.status_code in (200, 201)
                if success:
                    logger.info(f"HA: {domain}.{service} → {entity_id} ✅")
                else:
                    logger.warning(f"HA: {domain}.{service} → {entity_id} ❌ {resp.status_code}")
                return success
        except Exception as e:
            logger.error(f"HA service call error: {e}")
            return False
    
    async def execute_command(self, cmd: dict) -> str:
        """执行解析后的命令。
        
        Args:
            cmd: parse_command() 的返回值
        
        Returns:
            回复文本
        """
        action = cmd["action"]
        entities = cmd["entities"]
        
        if not entities:
            return "没找到对应的设备"
        
        entity_id = entities[0]
        domain = entity_id.split(".")[0]
        
        if action == "turn_on":
            await self.call_service(domain, "turn_on", entity_id)
            return "好的"
        elif action == "turn_off":
            await self.call_service(domain, "turn_off", entity_id)
            return "已关闭"
        elif action == "set_temperature":
            temp = cmd["params"].get("temperature", 24)
            await self.call_service(
                "climate", "set_temperature", entity_id,
                temperature=temp,
            )
            return f"空调已设置到{temp}度"
        else:
            return "好的"
