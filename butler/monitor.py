"""Monitoring & Observability — 健康检查, 指标, 追踪。

Features:
  - /health 端点: 全模块状态
  - 性能指标: 每轮对话的延迟分布
  - 告警: 异常检测 + 通知
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("butler.monitor")


@dataclass
class TraceSpan:
    """单轮对话的追踪信息。"""
    conversation_id: str = ""
    user: str = ""
    device: str = ""
    intent: str = ""
    vad_ms: int = 0
    stt_ms: int = 0
    llm_first_ms: int = 0
    tts_first_ms: int = 0
    total_ms: int = 0
    token_count: int = 0
    error: str = ""
    timestamp: float = 0.0
    
    @property
    def success(self) -> bool:
        return not self.error
    
    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "user": self.user,
            "device": self.device,
            "intent": self.intent,
            "latency": {
                "vad": self.vad_ms,
                "stt": self.stt_ms,
                "llm_first": self.llm_first_ms,
                "tts_first": self.tts_first_ms,
                "total": self.total_ms,
            },
            "token_count": self.token_count,
            "error": self.error,
            "success": self.success,
            "timestamp": self.timestamp,
        }


class MetricsCollector:
    """指标收集器 — 滑动窗口统计。"""
    
    def __init__(self, window_seconds: int = 3600):
        self.window_seconds = window_seconds
        self._traces: deque[TraceSpan] = deque(maxlen=1000)
        self._module_health: dict[str, bool] = {
            "vad": True, "stt": True, "llm": True,
            "tts": True, "hass": True, "agent": True,
        }
        self._module_last_error: dict[str, float] = {}
    
    def record(self, span: TraceSpan):
        self._traces.append(span)
    
    def set_module_health(self, module: str, healthy: bool):
        self._module_health[module] = healthy
        if not healthy:
            self._module_last_error[module] = time.time()
    
    def get_health(self) -> dict:
        """获取全系统健康状态。"""
        now = time.time()
        recent = [t for t in self._traces if now - t.timestamp < self.window_seconds]
        
        total = len(recent)
        errors = sum(1 for t in recent if t.error)
        error_rate = errors / total if total > 0 else 0.0
        
        latencies = [t.total_ms for t in recent if t.total_ms > 0]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        
        # P50, P95, P99
        sorted_lat = sorted(latencies)
        p50 = sorted_lat[len(sorted_lat) // 2] if sorted_lat else 0
        p95 = sorted_lat[int(len(sorted_lat) * 0.95)] if sorted_lat else 0
        p99 = sorted_lat[int(len(sorted_lat) * 0.99)] if sorted_lat else 0
        
        return {
            "status": "ok" if error_rate < 0.05 else "degraded",
            "uptime": self._get_uptime(),
            "modules": {
                name: {
                    "status": "ok" if healthy else "error",
                    "last_error_ago": f"{int(now - self._module_last_error.get(name, 0))}s"
                    if name in self._module_last_error else None,
                }
                for name, healthy in self._module_health.items()
            },
            "conversations_total": total,
            "error_rate_24h": f"{error_rate * 100:.1f}%",
            "latency": {
                "avg_ms": int(avg_latency),
                "p50_ms": p50,
                "p95_ms": p95,
                "p99_ms": p99,
            },
        }
    
    def _get_uptime(self) -> str:
        """获取系统运行时间。"""
        try:
            import subprocess
            r = subprocess.run(
                ["uptime"], capture_output=True, text=True, timeout=3
            )
            return r.stdout.strip()
        except Exception:
            return "unknown"
    
    def get_recent_conversations(self, limit: int = 20) -> list[dict]:
        """获取最近的对话记录。"""
        recent = list(self._traces)[-limit:]
        return [t.to_dict() for t in reversed(recent)]


class AlertManager:
    """告警管理器 — 检测异常并通知。"""
    
    def __init__(self):
        self._alerts: list[dict] = []
    
    def check_and_alert(self, metrics: dict) -> list[str]:
        """检查是否需要触发告警。
        
        Returns:
            需要触发的告警列表
        """
        alerts = []
        
        # 高错误率
        error_rate_str = metrics.get("error_rate_24h", "0%")
        error_rate = float(error_rate_str.strip("%"))
        if error_rate > 5:
            alerts.append(f"高错误率: {error_rate}%")
        
        # 高延迟
        p99 = metrics.get("latency", {}).get("p99_ms", 0)
        if p99 > 5000:
            alerts.append(f"高延迟 P99: {p99}ms")
        
        # 模块异常
        for module, info in metrics.get("modules", {}).items():
            if info.get("status") == "error":
                alerts.append(f"模块异常: {module}")
        
        for alert in alerts:
            self._alerts.append({
                "message": alert,
                "timestamp": time.time(),
            })
        
        return alerts


# Global instances
metrics = MetricsCollector()
alerts = AlertManager()
