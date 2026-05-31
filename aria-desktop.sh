#!/bin/bash
# Aria 桌面客户端 — 菜单栏助手 (类 Siri)
# 运行前需要 Aria 服务器已经在运行

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# 激活虚拟环境
source venv/bin/activate

# 从环境变量读取配置
SERVER="${ARIA_SERVER:-wss://127.0.0.1:8653}"
KEYWORD="${ARIA_WAKE_KEYWORD:-computer}"

echo "🚀 Aria Desktop Client"
echo "   Server: $SERVER"
echo "   Wake keyword: $KEYWORD"
echo ""
echo "📍 图标将出现在菜单栏"
echo "   - 点击菜单栏图标切换唤醒"
echo "   - 或使用菜单手动触发对话"
echo ""

exec python3 desktop/client.py --server "$SERVER" --keyword "$KEYWORD"
