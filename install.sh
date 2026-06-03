#!/usr/bin/env bash
set -euo pipefail

echo "=== ApexCrawler 安装脚本 ==="

# 检查 Python 版本
PYTHON=$(command -v python3 || command -v python)
if [ -z "$PYTHON" ]; then
    echo "ERROR: 未找到 python3"
    exit 1
fi

PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$("$PYTHON" -c "import sys; print(sys.version_info.major)")
PY_MINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    echo "ERROR: 需要 Python >= 3.11, 当前: $PY_VER"
    exit 1
fi
echo "[OK] Python $PY_VER"

# 创建虚拟环境（如果不存在）
if [ ! -d ".venv" ]; then
    echo "创建虚拟环境..."
    "$PYTHON" -m venv .venv
    echo "[OK] .venv 创建完毕"
else
    echo "[SKIP] .venv 已存在"
fi

# 激活虚拟环境
source .venv/bin/activate
echo "[OK] 虚拟环境已激活"

# 安装核心包
echo "安装 apexcrawler + 核心依赖..."
pip install -e "." > /dev/null 2>&1 || {
    echo "WARN: pip install -e . 失败，尝试直接安装..."
    pip install -r requirements.txt
}
echo "[OK] 核心包安装完成"

# 安装 playwright 浏览器
echo "安装 Playwright Chromium 浏览器..."
python -m playwright install chromium 2>/dev/null || {
    echo "WARN: playwright install 失败，请手动运行: playwright install chromium"
}
echo "[OK] Playwright 浏览器检查完成"

# 可选：开发依赖
echo ""
echo "--- 可选依赖 ---"
echo "开发工具:   pip install -e '.[dev]'"
echo "AI 能力:    pip install -e '.[ai]'"
echo "OCR 增强:   pip install -e '.[ocr]'"
echo ""

# 验证安装
echo "验证安装..."
python -c "import apexcrawler; print(f'[OK] ApexCrawler {apexcrawler.__version__ if hasattr(apexcrawler, \"__version__\") else \"\"} 安装成功')" 2>/dev/null || {
    echo "WARN: 导入验证失败，但包可能仍可用"
}

echo ""
echo "=== 安装完成 ==="
echo "启动面板:  apex dashboard"
echo "快速测试:  apex ask '提取 github.com 首页标题'"
