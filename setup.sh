#!/bin/bash
# ApexCrawler 一键安装脚本 (Mac/Linux)
# 用法: bash setup.sh

set -e
echo "🎛️  安装 ApexCrawler..."

# pip 依赖
pip3 install httpx pydantic pydantic-settings click fastapi uvicorn aiohttp -q

# 快捷命令
mkdir -p ~/bin
cat > ~/bin/apex << 'ALIAS'
#!/bin/bash
DIR="$(cd "$(dirname "$0")" && cd .. && pwd)"
cd "$DIR" 2>/dev/null || cd ~/ApexCrawler-
PYTHONPATH=. python3 -m apexcrawler.cli.main "$@"
ALIAS
chmod +x ~/bin/apex
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zshrc 2>/dev/null || echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc

echo "✅ 完成！新终端里敲: apex crawl https://huaspeed.cc"
