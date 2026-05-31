# ApexCrawler 🎛️

开源界唯一 **JS 层 + 网络层双覆盖** 的顶级爬虫框架。

## 一句话

```bash
apex ask "提取 amazon.com iPhone 15 的价格和评分"
```

## 快速开始

```bash
pip install -e ".[dev]"
playwright install chromium
apex dashboard
```

## 核心能力

- 4 引擎自动路由 (CloakBrowser / Camoufox / PatchRight / Playwright)
- JA4 TLS 指纹 + HTTP/2 帧指纹
- LLM 语义提取 + 自然语言输入
- 字体反爬破解 (WOFF2 + OCR)
- 人类行为模拟 (贝塞尔鼠标 + 变速键盘)
- Web 面板 + 可视化点选 + 模板系统
- 6 阶段管线 + 回滚 + 自适应速率

## 架构

`ApexCrawler_最终版方案.md` — 567 行完整方案，15 位专家评审

## 许可

MIT — 教育研究用途
