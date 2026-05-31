#!/bin/bash
# Aria Desktop Client — 双击运行 (macOS .command 文件)

cd "$(dirname "$0")"
source venv/bin/activate

# 设置默认参数
export ARIA_SERVER="${ARIA_SERVER:-wss://127.0.0.1:8653/ws}"
export ARIA_WAKE_KEYWORD="${ARIA_WAKE_KEYWORD:-computer}"

echo "🚀 Aria Desktop Client"
echo "   Server: $ARIA_SERVER"
echo "   Wake keyword: $ARIA_WAKE_KEYWORD"
echo ""
echo "📍 菜单栏图标将出现"
echo "   关闭此终端窗口即退出"
echo ""

exec python3 -u desktop/client.py
