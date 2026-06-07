# ApexCrawler

自适应网页爬虫框架 — 专为反爬对抗场景设计。5 引擎自动路由、8 层指纹注入、6 阶段管线、人类行为模拟、起点中文网专项。

## 核心能力

### 隐身技术
- 8 层 CDP 指纹注入（webdriver/navigator/WebGL/Canvas/AudioContext/plugins/mimeTypes/Resources）
- 5 套 DeviceProfile（win_chrome_124/131、mac_chrome、win_firefox、mac_safari、cn_win_chrome_120）
- 动态 UA 生成 + Client Hints
- JA4 TLS 指纹模拟（curl_cffi Chrome 131）
- 降级链：vanilla → patched → camoufox → cloaked

### 5 引擎系统

| 引擎 | 用途 |
|------|------|
| vanilla | 标准 Playwright Chromium |
| patched | Playwright + Stealth JS 注入 |
| camoufox | Firefox Camoufox 反检测浏览器 |
| cloaked | 低层 CDP + WASM 拦截 |
| qidian | 🆕 起点中文网专用（WAF 绕过 + Cookie 管理）|

### CLI 命令
- `apex crawl` — 完整 6 阶段管线爬取
- `apex extract` — 一键提取（轻量，无需浏览器）
- `apex ask` — 自然语言描述爬取
- `apex shell` — 🆕 交互式 Shell
- `apex qidian` — 🆕 起点中文网（login/info/crawl）
- `apex checkpoints` — 🆕 断点续爬管理
- `apex config` — 配置管理
- `apex dashboard` — Web 面板
- `apex visual` — 可视化点选
- `apex template` — 提取模板

### MCP 服务器

```bash
# 启动 MCP 服务器（供 AI 工具调用）
python3 -m apexcrawler.mcp_server

# 在 Claude Code 中注册
claude mcp add ApexCrawler "python3 /path/to/apexcrawler/mcp_server.py"
```

支持 4 个工具：crawl / extract / qidian_list / qidian_crawl

### 快速开始

```bash
pip install -e .
apex version
apex crawl https://example.com --fast
```

### 安装要求
- Python >= 3.11
- Playwright 浏览器（`playwright install chromium`）
- 可选：`pip install mcp`（MCP 服务器功能）

## 许可

MIT — 教育研究用途
