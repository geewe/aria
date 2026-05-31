"""Network connectivity checker — 快速检测网络是否可用。

提供三层检测:
1. 本地缓存 (30s 内重复查询直接返回缓存结果)
2. TCP 快速探测 (1s 超时, 连接 API 端口)
3. DNS 解析检查 (作为补充)
"""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlparse

logger = logging.getLogger("butler.network")


class NetworkChecker:
    """网络连通性检查器 — 带缓存和分层检测。

    用法:
        checker = NetworkChecker()
        online = await checker.check("http://localhost:8642/v1/chat/completions")
    """

    def __init__(self, cache_ttl: float = 30.0, probe_timeout: float = 1.0):
        self._cache_ttl = cache_ttl
        self._probe_timeout = probe_timeout
        self._cache: dict[str, tuple[bool, float]] = {}  # url -> (is_online, timestamp)
        self._global_result: tuple[bool, float] | None = None  # (is_online, timestamp)

    async def check(self, target_url: str | None = None) -> bool:
        """检查网络是否可用。

        Args:
            target_url: 要检测的目标 URL。为 None 时仅返回全局缓存结果。

        Returns:
            True 表示网络可达, False 表示不可达。
        """
        now = time.time()

        # 优先使用全局缓存
        if self._global_result is not None:
            cached, ts = self._global_result
            if now - ts < self._cache_ttl:
                return cached

        # 检查特定 URL 缓存
        if target_url:
            if target_url in self._cache:
                cached, ts = self._cache[target_url]
                if now - ts < self._cache_ttl:
                    return cached

        # 执行探测
        result = await self._probe(target_url)

        # 更新缓存
        self._global_result = (result, now)
        if target_url:
            self._cache[target_url] = (result, now)

        return result

    async def _probe(self, target_url: str | None = None) -> bool:
        """执行实际的网络探测。

        先尝试 TCP 连接, 再尝试 DNS 解析。
        """
        if target_url:
            try:
                parsed = urlparse(target_url)
                host = parsed.hostname or "localhost"
                port = parsed.port or (443 if parsed.scheme == "https" else 80)

                # TCP 连接探测 (最快方式)
                try:
                    _, writer = await asyncio.wait_for(
                        asyncio.open_connection(host, port),
                        timeout=self._probe_timeout,
                    )
                    writer.close()
                    await writer.wait_closed()
                    logger.debug(f"Network: {host}:{port} reachable (TCP)")
                    return True
                except (asyncio.TimeoutError, OSError, ConnectionError):
                    pass
            except Exception:
                pass

        # 兜底: DNS 解析测试 + HTTP HEAD
        try:
            import httpx
            async with httpx.AsyncClient(timeout=self._probe_timeout) as client:
                # 用公网 DNS 或常见网站验证互联网连通性
                test_urls = [
                    "https://www.baidu.com",
                    "https://www.qq.com",
                ]
                for url in test_urls:
                    try:
                        r = await client.head(url, follow_redirects=False)
                        if r.status_code < 500:
                            logger.debug(f"Network: internet reachable ({url})")
                            return True
                    except Exception:
                        continue
        except Exception:
            pass

        logger.info("Network: unreachable")
        return False

    async def check_api(self, api_url: str) -> bool:
        """专门检测 LLM API 是否可达 (比通用探测更快)。

        直接尝试 TCP 连接到 API 服务器的 host:port, 超时 1s。
        """
        return await self.check(api_url)

    def invalidate_cache(self):
        """清除全部缓存 — 外部网络状态变化时调用。"""
        self._global_result = None
        self._cache.clear()
        logger.debug("Network cache cleared")


# 全局单例
network_checker = NetworkChecker()
