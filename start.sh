#!/bin/bash
# B站弹幕 Agent 启动脚本

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 检查 Python
if command -v python3 &> /dev/null; then
    PYTHON=python3
elif [ -f "$SCRIPT_DIR/venv/bin/python3" ]; then
    PYTHON="$SCRIPT_DIR/venv/bin/python3"
else
    echo "❌ 未找到 Python3，请先安装 Python 3.8+"
    exit 1
fi

# 检查虚拟环境
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    echo "📦 首次运行，正在创建虚拟环境..."
    $PYTHON -m venv venv
    PYTHON="$SCRIPT_DIR/venv/bin/python3"
    echo "📥 正在安装依赖..."
    $PYTHON -m pip install flask requests -q
fi

# 检查依赖
$PYTHON -c "import flask, requests" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "📥 正在安装依赖..."
    $PYTHON -m pip install flask requests -q
fi

echo "🚀 启动 B站弹幕 Agent..."
echo "📱 访问地址: http://localhost:5000"
echo "⚠️  按 Ctrl+C 停止服务"
echo ""

$PYTHON app.py
