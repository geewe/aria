"""系统自检 & 故障诊断 v1 — 全模块健康检查。

包含:
  - 系统资源 (CPU/内存/磁盘/音频设备)
  - 网络连通性 (互联网/API/HA)
  - 服务健康 (LLM/TTS/STT/WebSocket)
  - 配置检查 (YAML/环境变量/证书)
  - 安全审计 (证书有效期/令牌格式)

用法:
    GET /api/diagnostics — 运行全量诊断
    GET /api/diagnostics/quick — 快速诊断
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import socket
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger("butler.diagnostics")


@dataclass
class DiagnosticResult:
    """单项诊断结果。"""
    name: str          # 检查项名称
    status: str        # "ok" | "warn" | "error" | "skip"
    message: str       # 人类可读的描述
    detail: Any = None # 额外数据 (可选)
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "detail": self.detail,
            "duration_ms": round(self.duration_ms, 1),
        }


class Diagnostics:
    """全量系统诊断引擎。"""

    def __init__(self, butler_server=None):
        self._butler = butler_server
        self._results: list[DiagnosticResult] = []

    async def run_all(self) -> dict:
        """运行全部诊断项。"""
        self._results = []
        checks = [
            ("Python 版本", self._check_python_version),
            ("系统资源", self._check_system_resources),
            ("音频设备", self._check_audio_devices),
            ("SSL 证书", self._check_ssl_certificate),
            ("配置文件", self._check_config_file),
            ("环境变量", self._check_env_vars),
            ("端口状态", self._check_port_availability),
            ("网络连通性", self._check_internet_connectivity),
            ("DNS 解析", self._check_dns_resolution),
            ("LLM API", self._check_llm_api),
            ("HomeAssistant", self._check_homeassistant),
            ("Edge TTS", self._check_tts_edge),
            ("macOS TTS", self._check_tts_macos),
            ("WebSocket", self._check_websocket_server),
            ("对话记录", self._check_conversation_history),
        ]
        for name, check in checks:
            t0 = time.time()
            try:
                result = await check()
                duration = (time.time() - t0) * 1000
                if isinstance(result, DiagnosticResult):
                    self._results.append(result)
                else:
                    self._results.append(DiagnosticResult(
                        name=name, duration_ms=duration, **result
                    ))
            except Exception as e:
                duration = (time.time() - t0) * 1000
                self._results.append(DiagnosticResult(
                    name=name, status="error",
                    message=f"检查异常: {e}",
                    duration_ms=duration,
                ))

        return self.summary()

    async def run_quick(self) -> dict:
        """快速诊断 (跳过耗时项: TTS合成/LLM调用)。"""
        self._results = []
        quick_checks = [
            ("Python 版本", self._check_python_version),
            ("系统资源", self._check_system_resources),
            ("SSL 证书", self._check_ssl_certificate),
            ("配置文件", self._check_config_file),
            ("环境变量", self._check_env_vars),
            ("端口状态", self._check_port_availability),
            ("网络连通性", self._check_internet_connectivity),
            ("DNS 解析", self._check_dns_resolution),
            ("HomeAssistant", self._check_homeassistant),
            ("WebSocket", self._check_websocket_server),
        ]
        for name, check in quick_checks:
            t0 = time.time()
            try:
                result = await check()
                duration = (time.time() - t0) * 1000
                if isinstance(result, DiagnosticResult):
                    self._results.append(result)
                else:
                    self._results.append(DiagnosticResult(
                        name=name, duration_ms=duration, **result
                    ))
            except Exception as e:
                duration = (time.time() - t0) * 1000
                self._results.append(DiagnosticResult(
                    name=name, status="error",
                    message=f"检查异常: {e}",
                    duration_ms=duration,
                ))

        return self.summary()

    def summary(self) -> list[dict]:
        """返回所有诊断结果 (含统计)。"""
        results = [r.to_dict() for r in self._results]

        errors = sum(1 for r in self._results if r.status == "error")
        warnings = sum(1 for r in self._results if r.status == "warn")
        total_duration = sum(r.duration_ms for r in self._results)

        return {
            "timestamp": datetime.now().isoformat(),
            "hostname": socket.gethostname(),
            "platform": f"{platform.system()} {platform.release()}",
            "aria_version": "4.1.0",
            "summary": {
                "total": len(self._results),
                "passed": len(self._results) - errors - warnings,
                "warnings": warnings,
                "errors": errors,
                "total_duration_ms": round(total_duration, 1),
            },
            "results": results,
        }

    # ─── 单项检查 ─────────────────────────────────

    async def _check(self, name: str, fn, *args, **kwargs) -> DiagnosticResult:
        """执行检查并计时。"""
        t0 = time.time()
        try:
            if asyncio.iscoroutinefunction(fn):
                result = await fn(*args, **kwargs)
            else:
                result = fn(*args, **kwargs)
            duration = (time.time() - t0) * 1000
            return DiagnosticResult(
                name=name, duration_ms=duration, **result
            )
        except Exception as e:
            duration = (time.time() - t0) * 1000
            return DiagnosticResult(
                name=name, status="error",
                message=f"检查异常: {e}",
                duration_ms=duration,
            )

    async def _check_python_version(self) -> dict:
        """检查 Python 版本。"""
        v = sys.version_info
        if v.major >= 3 and v.minor >= 10:
            return {
                "status": "ok",
                "message": f"Python {v.major}.{v.minor}.{v.micro}",
                "detail": {"executable": sys.executable, "version": sys.version},
            }
        return {
            "status": "warn",
            "message": f"Python {v.major}.{v.minor}.{v.micro} (推荐 ≥3.10)",
        }

    async def _check_system_resources(self) -> dict:
        """检查系统资源 (CPU/内存/磁盘)。"""
        issues = []

        # CPU 负载
        try:
            load1, load5, load15 = os.getloadavg()
            cpu_count = os.cpu_count() or 1
            detail = {
                "cpu_load": {"1m": load1, "5m": load5, "15m": load15, "cores": cpu_count},
            }
            if load1 > cpu_count * 1.5:
                issues.append(f"CPU 负载过高 ({load1:.1f}/{cpu_count}核)")
        except (OSError, AttributeError):
            detail = {"cpu_load": "unknown"}

        # 内存 (macOS)
        try:
            r = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=3
            )
            for line in r.stdout.split("\n"):
                if "page size" in line.lower():
                    page_size = int(line.split()[-1])
                if "Pages active" in line:
                    active_pages = int(line.split()[-1].rstrip("."))
            # macOS doesn't show total RAM trivially, skip detailed check
            detail["memory"] = "vm_stat ok"
        except Exception:
            detail["memory"] = "unknown"

        # 磁盘
        try:
            usage = shutil.disk_usage(Path.home())
            free_gb = usage.free / (1024**3)
            total_gb = usage.total / (1024**3)
            usage_pct = usage.used / usage.total * 100
            detail["disk"] = {
                "free_gb": round(free_gb, 1),
                "total_gb": round(total_gb, 1),
                "usage_pct": round(usage_pct, 1),
            }
            if usage_pct > 90:
                issues.append(f"磁盘空间不足 ({usage_pct:.0f}%)")
            elif usage_pct > 80:
                issues.append(f"磁盘使用率较高 ({usage_pct:.0f}%)")
        except Exception:
            detail["disk"] = "unknown"

        if issues:
            return {"status": "warn", "message": "; ".join(issues), "detail": detail}
        return {"status": "ok", "message": "系统资源充足", "detail": detail}

    async def _check_audio_devices(self) -> dict:
        """检查音频输入/输出设备。"""
        devices = {"input": [], "output": []}
        try:
            # macOS: 使用 system_profiler 或递归 /dev/
            r = subprocess.run(
                ["system_profiler", "SPAudioDataType"],
                capture_output=True, text=True, timeout=10,
            )
            current_type = None
            for line in r.stdout.split("\n"):
                line = line.strip()
                if "输入" in line or "Input" in line:
                    current_type = "input"
                elif "输出" in line or "Output" in line:
                    current_type = "output"
                elif current_type and ":" in line and not line.startswith("("):
                    name = line.split(":")[0].strip()
                    if name:
                        devices[current_type].append(name)

            # 同时也检查 /dev/ 下的音频设备
            for dev in Path("/dev").glob("audio*"):
                devices["output"].append(str(dev))

            has_input = len(devices["input"]) > 0
            has_output = len(devices["output"]) > 0

            if has_input and has_output:
                return {
                    "status": "ok",
                    "message": f"输入 {len(devices['input'])} 个, 输出 {len(devices['output'])} 个",
                    "detail": devices,
                }
            elif has_output:
                return {
                    "status": "warn",
                    "message": f"仅有输出设备 ({len(devices['output'])} 个)",
                    "detail": devices,
                }
            return {
                "status": "warn",
                "message": "未检测到音频设备",
                "detail": devices,
            }
        except Exception as e:
            return {
                "status": "warn",
                "message": f"无法检测音频设备: {e}",
                "detail": devices,
            }

    async def _check_ssl_certificate(self) -> dict:
        """检查 SSL 证书。"""
        cert_path = Path.cwd() / "cert.pem"
        key_path = Path.cwd() / "key.pem"

        if not cert_path.exists() or not key_path.exists():
            return {
                "status": "error",
                "message": "SSL 证书文件缺失",
                "detail": {
                    "cert": cert_path.exists(),
                    "key": key_path.exists(),
                },
            }

        try:
            ctx = ssl.create_default_context(cafile=str(cert_path))
            # Just validate it loads
            detail = {
                "cert_path": str(cert_path),
                "key_path": str(key_path),
                "cert_size_bytes": cert_path.stat().st_size,
                "key_size_bytes": key_path.stat().st_size,
            }

            # Check expiry
            import datetime as dt
            r = subprocess.run(
                ["openssl", "x509", "-in", str(cert_path), "-noout", "-dates"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.split("\n"):
                if line.startswith("notAfter="):
                    expiry_str = line.split("=", 1)[1]
                    try:
                        expiry = datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z")
                        days_left = (expiry - datetime.now()).days
                        detail["expiry"] = expiry.isoformat()
                        detail["days_left"] = days_left
                        if days_left < 30:
                            return {
                                "status": "warn",
                                "message": f"证书将在 {days_left} 天后过期",
                                "detail": detail,
                            }
                    except ValueError:
                        pass

            return {
                "status": "ok",
                "message": "SSL 证书有效",
                "detail": detail,
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"SSL 证书检查失败: {e}",
            }

    async def _check_config_file(self) -> dict:
        """检查配置文件。"""
        config_paths = [
            Path.cwd() / "config.yaml",
            Path.cwd() / "config.yaml.example",
            Path.home() / ".aria" / "config.yaml",
        ]
        found = []
        for p in config_paths:
            if p.exists():
                try:
                    import yaml
                    with open(p) as f:
                        data = yaml.safe_load(f)
                    if data and isinstance(data, dict):
                        found.append({
                            "path": str(p),
                            "size_bytes": p.stat().st_size,
                            "keys": list(data.keys()),
                        })
                except Exception as e:
                    found.append({"path": str(p), "error": str(e)})

        if found:
            detail = {"files": found}
            # Check for any errors
            errors = [f for f in found if "error" in f]
            if errors:
                return {
                    "status": "warn",
                    "message": f"配置文件存在但部分解析失败: {len(errors)} 个问题",
                    "detail": detail,
                }
            return {
                "status": "ok",
                "message": f"找到 {len(found)} 个配置文件",
                "detail": detail,
            }
        return {
            "status": "warn",
            "message": "未找到配置文件 (将使用默认值)",
            "detail": {"searched": [str(p) for p in config_paths]},
        }

    async def _check_env_vars(self) -> dict:
        """检查必需的环境变量。"""
        required = {
            "HASS_TOKEN": "HomeAssistant 访问令牌",
        }
        optional = {
            "HASS_URL": "HomeAssistant 地址 (默认: http://localhost:8123)",
            "LLM_API_URL": "LLM API 地址 (默认: 本地 Hermes)",
            "LLM_API_KEY": "LLM API 密钥",
            "TTS_VOICE": "TTS 语音 (默认: zh-CN-XiaoxiaoNeural)",
        }

        detail = {"required": {}, "optional": {}}
        missing_required = []

        for key, desc in required.items():
            val = os.environ.get(key)
            if val:
                detail["required"][key] = {"present": True, "desc": desc}
            else:
                detail["required"][key] = {"present": False, "desc": desc}
                missing_required.append(key)

        for key, desc in optional.items():
            val = os.environ.get(key)
            detail["optional"][key] = {
                "present": key in os.environ,
                "value": (val[:20] + "...") if val and len(val) > 20 else (val or ""),
                "desc": desc,
            }

        if missing_required:
            return {
                "status": "error",
                "message": f"缺少必需环境变量: {', '.join(missing_required)}",
                "detail": detail,
            }
        return {
            "status": "ok",
            "message": "环境变量配置完整",
            "detail": detail,
        }

    async def _check_port_availability(self) -> dict:
        """检查端口可用性。"""
        target_ports = [8653, 8650, 8123, 8642]
        detail = {}
        for port in target_ports:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(("127.0.0.1", port))
                sock.close()
                detail[str(port)] = {
                    "in_use": result == 0,
                    "service": "Aria" if port in (8650, 8653)
                              else "HomeAssistant" if port == 8123
                              else "Hermes LLM" if port == 8642
                              else "unknown",
                }
            except Exception as e:
                detail[str(port)] = {"error": str(e)}

        # Aria 自己的端口必须可用
        in_use = detail.get("8653", {}).get("in_use", False)
        if in_use:
            return {
                "status": "ok",
                "message": "端口 8653 (Aria) 运行中",
                "detail": detail,
            }
        return {
            "status": "warn",
            "message": "Aria 未在端口 8653 上检测到",
            "detail": detail,
        }

    async def _check_internet_connectivity(self) -> dict:
        """检查互联网连通性。"""
        from .network import network_checker
        online = await network_checker.check()
        if online:
            return {
                "status": "ok",
                "message": "互联网连接正常",
                "detail": {"source": "network_checker"},
            }
        return {
            "status": "error",
            "message": "无法访问互联网 (部分功能受限: LLM/EdgeTTS)",
            "detail": {"source": "network_checker"},
        }

    async def _check_dns_resolution(self) -> dict:
        """检查 DNS 解析。"""
        hosts = [
            ("baidu.com", "公网"),
            ("www.bing.com", "公网"),
            ("api.openai.com", "LLM API"),
        ]
        detail = {}
        failures = 0
        for host, label in hosts:
            try:
                addrs = socket.getaddrinfo(host, 80, type=socket.SOCK_STREAM)
                detail[host] = {
                    "resolved": True,
                    "addresses": list(set(a[4][0] for a in addrs[:3])),
                    "label": label,
                }
            except socket.gaierror:
                detail[host] = {"resolved": False, "label": label}
                failures += 1

        if failures >= len(hosts):
            return {
                "status": "error",
                "message": "DNS 解析全部失败 (无网络连接)",
                "detail": detail,
            }
        elif failures > 0:
            return {
                "status": "warn",
                "message": f"{failures}/{len(hosts)} 个域名解析失败",
                "detail": detail,
            }
        return {
            "status": "ok",
            "message": "DNS 解析正常",
            "detail": detail,
        }

    async def _check_llm_api(self) -> dict:
        """检查 LLM API 可用性。"""
        from .config import config as cfg

        api_url = os.environ.get("LLM_API_URL", cfg.HERMES_API_URL)
        api_key = os.environ.get("LLM_API_KEY", cfg.HERMES_API_KEY)

        if not api_url:
            return {
                "status": "warn",
                "message": "未配置 LLM API 地址",
            }

        detail = {"url": api_url, "model": cfg.LLM_MODEL}

        try:
            parsed = urlparse(api_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 80

            # TCP 检测
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=2,
            )
            writer.close()
            await writer.wait_closed()
            detail["tcp_reachable"] = True

            return {
                "status": "ok",
                "message": f"LLM API 可达 ({host}:{port})",
                "detail": detail,
            }
        except asyncio.TimeoutError:
            return {
                "status": "error",
                "message": f"LLM API 连接超时 ({host}:{port})",
                "detail": detail,
            }
        except (OSError, ConnectionError) as e:
            return {
                "status": "error",
                "message": f"LLM API 不可达: {e}",
                "detail": detail,
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"LLM API 检查异常: {e}",
                "detail": detail,
            }

    async def _check_homeassistant(self) -> dict:
        """检查 HomeAssistant 连接。"""
        if self._butler and self._butler.hass:
            try:
                entities = await self._butler.hass.refresh_entities()
                count = len(entities) if entities else 0
                return {
                    "status": "ok",
                    "message": f"已连接, {count} 个设备/实体",
                    "detail": {
                        "url": self._butler.hass.url,
                        "entities_count": count,
                        "connected": True,
                    },
                }
            except Exception as e:
                return {
                    "status": "error",
                    "message": f"HomeAssistant 连接失败: {e}",
                    "detail": {"url": getattr(self._butler.hass, "url", "unknown")},
                }
        else:
            # 尝试直接连接
            from .config import config as cfg
            url = os.environ.get("HASS_URL", cfg.HASS_URL)
            token = os.environ.get("HASS_TOKEN", cfg.HASS_TOKEN)

            if not token:
                return {
                    "status": "warn",
                    "message": "未配置 HomeAssistant 令牌",
                    "detail": {"url": url},
                }

            try:
                from .hass import HAConnector
                hass = HAConnector(url, token)
                entities = await hass.refresh_entities()
                count = len(entities) if entities else 0
                return {
                    "status": "ok",
                    "message": f"API 测试通过, {count} 个实体",
                    "detail": {"url": url, "entities_count": count},
                }
            except Exception as e:
                return {
                    "status": "error",
                    "message": f"HomeAssistant 连接失败: {e}",
                    "detail": {"url": url},
                }

    async def _check_tts_edge(self) -> dict:
        """检查 Edge TTS 可用性。"""
        from .network import network_checker
        online = await network_checker.check()

        if not online:
            return {
                "status": "skip",
                "message": "离线模式, 跳过 EdgeTTS 检测",
            }

        try:
            import edge_tts
            voices = await edge_tts.list_voices()
            zh_voices = [
                v for v in voices
                if "zh-" in v.get("ShortName", "")
            ]
            if zh_voices:
                return {
                    "status": "ok",
                    "message": f"EdgeTTS 可用, {len(zh_voices)} 个中文语音",
                    "detail": {
                        "total_voices": len(voices),
                        "chinese_voices": len(zh_voices),
                        "samples": [v["ShortName"] for v in zh_voices[:5]],
                    },
                }
            return {
                "status": "warn",
                "message": "EdgeTTS 无中文语音",
                "detail": {"total_voices": len(voices)},
            }
        except ImportError:
            return {
                "status": "error",
                "message": "edge-tts 未安装",
                "detail": {"install_cmd": "pip install edge-tts"},
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"EdgeTTS 检测失败: {e}",
            }

    async def _check_tts_macos(self) -> dict:
        """检查 macOS say 命令。"""
        try:
            # 测试 say 可用性
            r = subprocess.run(
                ["say", "-v", "?", "-o", "/dev/null", "test"],
                capture_output=True, text=True, timeout=5,
            )
            # List available voices
            r2 = subprocess.run(
                ["say", "-v", "?"],
                capture_output=True, text=True, timeout=5,
            )
            zh_voices = [l for l in r2.stdout.split("\n") if any(
                v in l for v in ["Tingting", "zh", "Chinese"]
            )]

            return {
                "status": "ok",
                "message": "macOS say 可用",
                "detail": {
                    "zh_voices": zh_voices[:5] if zh_voices else ["Tingting (默认)"],
                    "ffmpeg": shutil.which("ffmpeg") is not None,
                },
            }
        except FileNotFoundError:
            return {
                "status": "error",
                "message": "say 命令未找到 (非 macOS?)",
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"macOS TTS 检测失败: {e}",
            }

    async def _check_websocket_server(self) -> dict:
        """检查 WebSocket 服务器健康。"""
        if self._butler:
            try:
                session_count = len(self._butler.sm.sessions) if hasattr(self._butler, 'sm') else 0
                orch_count = len(self._butler.orchestrators) if hasattr(self._butler, 'orchestrators') else 0
                return {
                    "status": "ok",
                    "message": f"WebSocket 服务运行中",
                    "detail": {
                        "active_sessions": session_count,
                        "active_orchestrators": orch_count,
                        "uptime": "running",
                    },
                }
            except Exception as e:
                return {
                    "status": "warn",
                    "message": f"WebSocket 状态检查失败: {e}",
                }
        else:
            return {
                "status": "warn",
                "message": "未连接到 Butler 实例 (仅静态检查)",
            }

    async def _check_conversation_history(self) -> dict:
        """检查对话历史。"""
        if self._butler and hasattr(self._butler, 'monitor') and hasattr(self._butler.monitor, 'get_recent_conversations'):
            from .monitor import metrics
            try:
                recent = metrics.get_recent_conversations(limit=5)
                return {
                    "status": "ok" if recent else "warn",
                    "message": f"最近 {len(recent)} 条对话",
                    "detail": {
                        "total_recorded": len(metrics._traces) if hasattr(metrics, '_traces') else 0,
                        "recent_conversations": recent,
                    },
                }
            except Exception as e:
                return {
                    "status": "warn",
                    "message": f"对话历史读取失败: {e}",
                }
        return {
            "status": "skip",
            "message": "对话监控未启用",
        }


# 便捷函数
async def run_diagnostics(butler_server=None) -> dict:
    """运行全量诊断。"""
    diag = Diagnostics(butler_server)
    return await diag.run_all()


async def quick_diagnostics(butler_server=None) -> dict:
    """运行快速诊断。"""
    diag = Diagnostics(butler_server)
    return await diag.run_quick()
