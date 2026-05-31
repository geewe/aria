"""Aria 家庭助手 v4 — 启动入口。

Usage:
  python run.py                    # 默认启动
  python run.py --host 0.0.0.0     # 监听所有接口
  python run.py --port 8650        # 指定端口
  python run.py --reload           # 热重载 (开发模式)
"""

import argparse
import logging
import os
import sys

# 确保 butler 包可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 加载 .env 文件
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                key, _, val = line.partition('=')
                if key and val:
                    os.environ.setdefault(key.strip(), val.strip())

from butler.config import config
from butler.server import app


def setup_logging():
    """配置日志。"""
    log_format = (
        "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s"
    )
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )
    # 调整第三方日志级别
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiosignal").setLevel(logging.WARNING)


def main():
    parser = argparse.ArgumentParser(description="Aria 家庭助手 v4")
    parser.add_argument("--host", default=config.HOST, help="监听地址")
    parser.add_argument("--port", type=int, default=config.PORT, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="热重载")
    parser.add_argument("--log-level", default="info",
                        choices=["debug", "info", "warning", "error"])
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger("butler")

    if args.log_level:
        logging.getLogger("butler").setLevel(args.log_level.upper())

    print()
    print("🏠 Aria 家庭助手 v4")
    print("=" * 50)
    print(f"  WebSocket:  ws://{args.host}:{args.port}/ws")
    print(f"  Health:     http://{args.host}:{args.port}/health")
    print(f"  Metrics:    http://{args.host}:{args.port}/metrics")
    print(f"  Push (GET): http://{args.host}:{args.port}/push?text=你好")
    print(f"  Web UI:     http://{args.host}:{args.port}/")
    print("=" * 50)
    print()

    import uvicorn
    uvicorn.run(
        "butler.server:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
