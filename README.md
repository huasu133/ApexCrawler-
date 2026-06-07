# ApexCrawler

自适应网页爬虫框架 — 专为反爬对抗场景设计。8 引擎自动降级、3 层隐身注入（JS+CDP+C++）、7 阶段管线、WAF 绕过、小说专项下载。

## 快速开始

```bash
pip install -e .
apex get https://example.com                    # 快速抓取
apex get https://example.com -o text             # 提取纯文本
apex novel info https://book.qidian.com/info/107580    # 小说目录
apex novel download https://book.qidian.com/info/107580 -c 1-100  # 下载
```

## 🚀 最新功能

### 🕷️ 8 引擎降级链

| 引擎 | 技术 | 用途 |
|------|------|------|
| vanilla | Playwright Chromium | 标准浏览器渲染 |
| patched | Playwright + Stealth JS | 轻量隐身 |
| patched_undetected | undetected-chromedriver | 高级浏览器隐身 |
| camoufox | Firefox 伪装 | Cloudflare Turnstile 绕过 |
| cloaked | CDP + WASM 拦截 | 中等级别隐身 |
| cloaked_v2 | **CloakBrowser C++ 49 补丁** | 最高等级指纹隐藏 |
| pydoll | CDP 直连 | 无 WebDriver 检测 |
| qidian | 起点专用 | 腾讯云 WAF 绕过 + Cookie 管理 |

### 🦾 3 层隐身对抗

1. **JS 层** — 15 层注入（Permissions/mediaDevices/keyboard/clipboard/performance/CDC 清理等）
2. **CDP 层** — DeviceProfile 切换（Chrome 131/Firefox/Safari）
3. **C++ 层** — CloakBrowser 49 个 Chromium 补丁

### 📚 小说下载（3 站支持）

```bash
# 起点中文网（WAF 绕过）
apex novel info https://book.qidian.com/info/107580
apex novel download https://book.qidian.com/info/107580 -c 1-200

# 17k 小说网
apex novel info https://www.17k.com/book/3631088.html
apex novel download https://www.17k.com/book/3631088.html -c 1-50

# 笔趣阁（30+ 镜像站支持）
apex novel info https://www.biquge7.com/book/1/
apex novel download https://www.biquge7.com/book/1/ -c 1-500
```

### 🤖 AI 提取与 Agent

```bash
# LLM 提取页面数据
apex get https://example.com --llm openai/gpt-4o --instruction "提取商品信息"

# 网络搜索 + 自动爬取
apex search "Python 爬虫框架对比" --crawl 3 --llm "总结各框架特点"

# AI 自主研究助手（多步骤推理）
apex agent "搜索并对比三大云服务的定价方案"
```

### 🌐 Web 监控面板

```bash
# 本地启动
apex dashboard
# 浏览器打开 http://localhost:8000
```

功能：任务创建/暂停/恢复/取消、实时指标（总任务/运行中/已完成/失败）、3 秒自动刷新、SSE 实时推送。

### 🐳 Docker 部署

```bash
cd docker
docker-compose up --build
# 访问 http://localhost:8000
```

多阶段构建、Playwright + CloakBrowser 预下载、`shm_size=2gb`。

### 🔎 更多功能

```bash
apex search "关键词"            # 网络搜索
apex map site example.com      # 站点 URL 发现（sitemap 解析）
apex interact url --script actions.json  # 页面交互（点击/填写/滚动）
apex agent "你的问题"           # AI 自主研究
apex view url                  # 页面截图
apex save url                  # 保存到文件
```

### 🔌 MCP 服务器（24+ 工具）

```bash
python3 -m apexcrawler.mcp_server

# Claude Code 注册
claude mcp add ApexCrawler "python3 /path/to/apexcrawler/mcp_server.py"
```

支持工具：crawl / extract / crawl_site / batch_crawl / screenshot_url / search / map_site / novel_info / novel_download / crawl_metrics / pause_crawl / resume_crawl / cancel_crawl 等 24+ 个。

### 🐍 Python SDK

```python
from apexcrawler import get

html = get("https://example.com")
text = get("https://example.com", output="text")
```

## 安装要求

- Python >= 3.11
- `playwright install chromium`（浏览器引擎）
- CloakBrowser：`pip install cloakbrowser`（最高隐身等级，可选）
- Serper API Key：`export SERPER_API_KEY=xxx`（搜索功能，可选）
- OpenAI API Key：`export OPENAI_API_KEY=xxx`（Agent/LLM 提取，可选）

## 技术架构

### 7 阶段管线

Schedule → Route → Evade → Extract → Validate → Store → FontDecode

### 已集成技术

| 技术 | 用途 |
|------|------|
| Crawl4AI | LLM 提取 + BM25/Pruning 内容过滤 |
| CloakBrowser | C++ 级 Chrome 指纹补丁 |
| Camoufox | Firefox 隐私隐身 |
| curl-cffi | JA4 TLS 指纹模拟 |
| fonttools + ddddocr | 字体反爬 3 级解码 |
| TaskManager | SQLite 任务持久化 + 暂停/恢复/取消 |

## 许可

MIT — 教育研究用途
