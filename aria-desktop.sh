#!/bin/bash
# Aria 桌面客户端 — 菜单栏常驻助手 (类 Siri 体验)
# 在 screen 会话中运行以保证后台持久化

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# 激活虚拟环境
source venv/bin/activate

SERVER="${ARIA_SERVER:-wss://127.0.0.1:8653/ws}"
KEYWORD="${ARIA_WAKE_KEYWORD:-computer}"

# 检查是否已在运行
if screen -ls | grep -q aria-desktop; then
    echo "ℹ️  Aria Desktop 已在运行"
    echo "   重新连接: screen -r aria-desktop"
    echo "   停止: screen -S aria-desktop -X quit"
    exit 0
fi

echo "🚀 启动 Aria Desktop Client"
echo "   Server: $SERVER"
echo "   Wake keyword: $KEYWORD"
echo ""

screen -dmS aria-desktop bash -c "source venv/bin/activate && exec python3 -u desktop/client.py --server '$SERVER' --keyword '$KEYWORD'"
sleep 2

if screen -ls | grep -q aria-desktop; then
    echo "✅ 已启动! 菜单栏将出现 🔊 图标"
    echo "   管理: screen -r aria-desktop"
    echo "   停止: screen -S aria-desktop -X quit"
else
    echo "❌ 启动失败"
    exit 1
fi
