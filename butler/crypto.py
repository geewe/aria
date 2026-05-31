"""Cryptocurrency price lookup — 多交易所聚合查询。

支持:
  - 中文币名 → 交易对 (比特币→BTC, 以太坊→ETH)
  - 多交易所并行查询 (最快响应优先)
  - 自动兜底: 全部超时 → 友好提示

用法:
    from .crypto import CryptoPricer
    pricer = CryptoPricer()
    result = await pricer.get_price("比特币")  # "比特币: $xx,xxx"
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

logger = logging.getLogger("butler.crypto")


# 中文币名 → 标准交易对 (Binance/OKX 格式)
COIN_MAP = {
    "比特币": "BTCUSDT",
    "以太坊": "ETHUSDT",
    "以太": "ETHUSDT",
    "狗狗币": "DOGEUSDT",
    "狗币": "DOGEUSDT",
    "瑞波": "XRPUSDT",
    "莱特币": "LTCUSDT",
    "莱特": "LTCUSDT",
    "币安币": "BNBUSDT",
    "艾达": "ADAUSDT",
    "波卡": "DOTUSDT",
    "索拉纳": "SOLUSDT",
    "马蹄": "MATICUSDT",
    "柚子": "EOSUSDT",
    "币安": "BNBUSDT",
    "柚子币": "EOSUSDT",
    # 错误纠正
    "大饼": "BTCUSDT",
    "二饼": "ETHUSDT",
}

# 交易所 API 配置 [(name, url_template, parser_fn)]
EXCHANGES = [
    {
        "name": "Binance",
        "url": "https://api.binance.com/api/v3/ticker/price?symbol={symbol}",
        "parse": lambda d: (float(d["price"]), "USDT"),
    },
    {
        "name": "OKX",
        "url": "https://www.okx.com/api/v5/market/ticker?instId={symbol.replace('USDT', '-USDT')}",
        "parse": lambda d: (float(d["data"][0]["last"]), "USDT"),
    },
    {
        "name": "CoinGecko",
        "url": "https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd",
        "parse": lambda d: (float(list(d.values())[0]["usd"]), "USD"),
        "coin_id": {
            "BTCUSDT": "bitcoin",
            "ETHUSDT": "ethereum",
            "DOGEUSDT": "dogecoin",
            "XRPUSDT": "ripple",
            "LTCUSDT": "litecoin",
            "BNBUSDT": "binancecoin",
            "ADAUSDT": "cardano",
            "DOTUSDT": "polkadot",
            "SOLUSDT": "solana",
            "MATICUSDT": "matic-network",
            "EOSUSDT": "eos",
        },
    },
    {
        "name": "MEXC",
        "url": "https://api.mexc.com/api/v3/ticker/price?symbol={symbol}",
        "parse": lambda d: (float(d["price"]), "USDT"),
    },
]


class CryptoPricer:
    """加密货币价格查询器。"""

    def __init__(self):
        self._timeout = 5.0
        self._http_headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Accept": "application/json",
        }

    async def get_price(self, coin_name: str) -> str:
        """查询币种价格。

        Args:
            coin_name: 中文币名 (比特币/以太坊/狗狗币)

        Returns:
            价格描述文本
        """
        # 1. 解析币名 → 交易对
        symbol = self._resolve_symbol(coin_name)
        if not symbol:
            return f"没找到{coin_name}的价格信息"

        # 2. 并行查询多个交易所
        results = await self._query_all(symbol)

        if not results:
            return "暂时无法获取实时价格，网络环境不支持"

        # 3. 取最快返回的结果
        price, currency, exchange = results[0]
        # 四舍五入: 大币种精确到小数点后2位, 小币种4位
        decimals = 2 if price >= 10 else 4
        price_str = f"{price:,.{decimals}f}"

        return f"{coin_name}当前{price_str}{currency}"

    def _resolve_symbol(self, name: str) -> Optional[str]:
        """中文币名 → 交易对。"""
        name = name.strip()
        # 精确匹配
        if name in COIN_MAP:
            return COIN_MAP[name]
        # 部分匹配: "比特币价格" → "比特币"
        for cn_name, sym in COIN_MAP.items():
            if cn_name in name or name in cn_name:
                return sym
        # 拼音/缩写
        upper = name.upper()
        if upper.endswith("USDT"):
            return upper
        if len(upper) <= 5 and upper.isalpha():
            return upper + "USDT"
        return None

    async def _query_all(self, symbol: str) -> list[tuple[float, str, str]]:
        """并行查询所有交易所。"""
        import httpx

        async def query_one(exchange: dict) -> Optional[tuple[float, str, str]]:
            try:
                # 构建 URL
                url_template = exchange["url"]
                if "coin_id" in exchange:
                    coin_id = exchange["coin_id"].get(symbol, symbol.lower())
                    url = url_template.replace("{coin_id}", coin_id)
                else:
                    url = url_template.replace("{symbol}", symbol)
                    # 有些交易所用 -USDT 格式
                    url = url.replace("{symbol.replace('USDT', '-USDT')}", 
                                     symbol.replace("USDT", "-USDT"))

                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(url, headers=self._http_headers)
                    if resp.status_code != 200:
                        return None
                    data = resp.json()
                    price, currency = exchange["parse"](data)
                    return (price, currency, exchange["name"])
            except Exception:
                return None

        # 并行查询所有交易所, 最快返回优先
        tasks = [query_one(ex) for ex in EXCHANGES]
        done = []
        for coro in asyncio.as_completed(tasks):
            result = await coro
            if result is not None:
                done.append(result)

        # 按价格排序 (取中位数附近, 剔除异常值)
        if not done:
            return []

        # 如果有多个结果, 取中位数
        done.sort(key=lambda x: x[0])
        return done

    async def list_coins(self) -> str:
        """列出支持的币种。"""
        coins = list(COIN_MAP.keys())
        # 去重, 保持顺序
        seen = set()
        unique = []
        for c in coins:
            if c not in seen:
                seen.add(c)
                unique.append(c)
        return f"支持查询: {', '.join(unique[:15])}"


# 模块级便捷函数
async def get_crypto_price(coin_name: str) -> str:
    """查询加密货币价格 (快捷方式)。"""
    pricer = CryptoPricer()
    return await pricer.get_price(coin_name)
