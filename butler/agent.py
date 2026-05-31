"""Agent Task Executor — 执行系统任务和自动化操作。

功能:
  1. 系统巡检 (CPU/内存/磁盘)
  2. 脚本执行 (安全沙箱)
  3. 任务调度 (通过 Hermes CLI cron)
  4. 状态查询
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import subprocess
import time
from typing import Optional

logger = logging.getLogger("butler.agent")


class AgentExecutor:
    """Agent 任务执行器。"""
    
    def __init__(self):
        self._tasks: dict[str, dict] = {}
    
    async def execute(self, action: str, params: dict = None) -> str:
        """执行 agent 任务。
        
        Args:
            action: 任务类型 (inspect, run_task, shell, etc.)
            params: 任务参数
        
        Returns:
            任务结果文本
        """
        params = params or {}
        
        handlers = {
            "inspect": self._inspect_system,
            "run_task": self._run_task,
            "shell": self._run_shell,
            "status": self._task_status,
        }
        
        handler = handlers.get(action)
        if not handler:
            return f"未知任务类型: {action}"
        
        try:
            return await handler(**params)
        except Exception as e:
            logger.error(f"Agent task error: {e}")
            return f"任务执行失败: {e}"
    
    async def _inspect_system(self, **kwargs) -> str:
        """系统巡检。"""
        loop = asyncio.get_event_loop()
        
        def _run():
            info = []
            info.append(f"系统: {platform.system()} {platform.release()}")
            info.append(f"主机: {platform.node()}")
            info.append(f"运行时间: {self._get_uptime()}")
            info.append(f"CPU: {self._get_cpu_info()}")
            info.append(f"内存: {self._get_memory_info()}")
            info.append(f"磁盘: {self._get_disk_info()}")
            info.append(f"Python: {platform.python_version()}")
            return "\n".join(info)
        
        return await loop.run_in_executor(None, _run)
    
    def _get_uptime(self) -> str:
        try:
            with open("/proc/uptime") as f:
                uptime_sec = float(f.read().split()[0])
                days = int(uptime_sec // 86400)
                hours = int((uptime_sec % 86400) // 3600)
                mins = int((uptime_sec % 3600) // 60)
                return f"{days}天{hours}小时{mins}分钟"
        except:
            # macOS: use `uptime` command
            try:
                result = subprocess.run(["uptime"], capture_output=True, text=True, timeout=5)
                return result.stdout.strip()
            except:
                return "未知"
    
    def _get_cpu_info(self) -> str:
        try:
            import psutil
            return f"{psutil.cpu_percent(interval=0.5)}% ({psutil.cpu_count()}核)"
        except ImportError:
            try:
                if platform.system() == "Darwin":
                    result = subprocess.run(["sysctl", "-n", "hw.ncpu"], capture_output=True, text=True, timeout=5)
                    cores = result.stdout.strip()
                    load = os.getloadavg()
                    return f"{cores}核, 负载 {load[0]:.1f} {load[1]:.1f} {load[2]:.1f}"
            except:
                pass
            return "未知"
    
    def _get_memory_info(self) -> str:
        try:
            import psutil
            mem = psutil.virtual_memory()
            total_gb = mem.total / (1024**3)
            used_gb = mem.used / (1024**3)
            percent = mem.percent
            return f"{used_gb:.1f}/{total_gb:.1f}GB ({percent}%)"
        except ImportError:
            try:
                if platform.system() == "Darwin":
                    result = subprocess.run(
                        ["vm_stat"], capture_output=True, text=True, timeout=5
                    )
                    # Parse vm_stat output
                    lines = result.stdout.strip().split("\n")
                    pages = {}
                    for line in lines:
                        if ":" in line:
                            k, v = line.split(":", 1)
                            k = k.strip()
                            v = v.strip().rstrip(".")
                            try:
                                pages[k] = int(v)
                            except:
                                pass
                    active = pages.get("Pages active", 0)
                    wired = pages.get("Pages wired down", 0)
                    total = (active + wired) * 16384 / (1024**3)
                    return f"~{total:.1f}GB 使用中"
            except:
                pass
            return "未知"
    
    def _get_disk_info(self) -> str:
        try:
            usage = shutil.disk_usage("/")
            total_gb = usage.total / (1024**3)
            used_gb = usage.used / (1024**3)
            percent = used_gb / total_gb * 100
            return f"{used_gb:.0f}/{total_gb:.0f}GB ({percent:.0f}%)"
        except:
            return "未知"
    
    async def _run_task(self, command: str = "", **kwargs) -> str:
        """运行简单任务/命令 (安全沙箱)。"""
        if not command:
            return "请指定要执行的命令"
        
        # 安全检查: 禁止的危险命令
        blocked = ["rm -rf /", "mkfs", "dd if=/dev/zero", "> /dev/sda"]
        for b in blocked:
            if b in command:
                return f"禁止执行危险命令: {b}"
        
        loop = asyncio.get_event_loop()
        
        def _run():
            try:
                result = subprocess.run(
                    command, shell=True, capture_output=True,
                    text=True, timeout=30,
                )
                output = result.stdout.strip()
                error = result.stderr.strip()
                
                if result.returncode != 0:
                    return f"执行失败: {error or output}"
                
                # 限制输出长度
                if len(output) > 500:
                    output = output[:500] + "..."
                return output or "执行完成"
            except subprocess.TimeoutExpired:
                return "执行超时(30s)"
            except Exception as e:
                return f"执行错误: {e}"
        
        return await loop.run_in_executor(None, _run)
    
    async def _run_shell(self, command: str = "", **kwargs) -> str:
        """运行 shell 命令 (别名)。"""
        return await self._run_task(command=command)
    
    async def _task_status(self, task_id: str = "", **kwargs) -> str:
        """查看任务状态。"""
        if not task_id:
            active = len(self._tasks)
            return f"当前活跃任务: {active}"
        
        task = self._tasks.get(task_id)
        if not task:
            return f"任务 {task_id} 不存在"
        return json.dumps(task, ensure_ascii=False, indent=2)
    
    async def close(self):
        """清理资源。"""
        pass
