@echo off
REM ApexCrawler 一键安装 (Windows)
REM 用法: 双击运行 或 setup.bat

echo 🎛️  安装 ApexCrawler...

pip install httpx pydantic pydantic-settings click fastapi uvicorn aiohttp -q

echo @echo off > "%USERPROFILE%\apex.bat"
echo cd /d %cd% >> "%USERPROFILE%\apex.bat"
echo set PYTHONPATH=. >> "%USERPROFILE%\apex.bat"
echo python -m apexcrawler.cli.main %%* >> "%USERPROFILE%\apex.bat"

echo ✅ 完成！
echo 用法: apex crawl https://huaspeed.cc
pause
