# ApexCrawler 完成与待办 — 2026-06-03

GitHub: https://github.com/huasu133/ApexCrawler-

---

## ✅ 已完成的修复 (4 个 commit)

| Commit | 内容 | 改动 |
|--------|------|------|
| `b83ca21` | 完整使用指南 + 引擎选择表 | docs |
| `7087538` | bare domain 自动加 https + Py3.9 兼容 | fix |
| `87d9130` | dashboard 缩进 bug 修复 | fix |
| `30b1309` | **4 专家审计 → 52 P0/P1 bug 修复** | 30 files, +455/-212 |
| `7c50569` | **7 个孤立模块接入 pipeline** | 11 files, +430/-32 |

---

## ✅ 已接入的模块 (7/11)

| 模块 | 接入位置 | 状态 |
|------|----------|------|
| DecisionEngine | RouteStage | ✅ 替代 _DefaultEngineMatcher |
| DeviceProfile | EvadeStage | ✅ WebGL/Canvas/Audio/字体指纹 |
| TimingScheduler | ScheduleStage | ✅ 内容驱动延迟 |
| ConnectionReuseManager | ExtractStage | ✅ proxy 复用 |
| SubresourceLoader | 4 engines | ✅ 子资源完整加载 |
| DNSCache | httpx 调用 | ✅ 3 处 (pipeline/CLI/mobile_sniffer) |
| PassiveSignalProfiler | Humanizer | ✅ 鼠标热区/滚动/会话 |

---

## ❌ 未接入的模块 (4 核心 + 10 可选)

### 🔴 核心功能需接入

| 模块 | 文件 | 应该做什么 |
|------|------|-----------|
| FontCracker + OCREngine + FontRecovery + DomFixer | anti_font/*.py | 新建 FontDecodeStage 到 pipeline |
| SelHealer | extraction/sel_healer.py | ExtractStage 失败时自愈选择器 |
| Cleaner | extraction/cleaner.py | 提取后数据清洗 |
| BrotliSupport | utils/brotli_support.py | httpx client brotli 解压 |

### 🟡 可选/独立模块

| 模块 | 文件 | 说明 |
|------|------|------|
| RateController | pipeline/rate_controller.py | 可选限流控制器 |
| SessionManager | pipeline/session_manager.py | 可选跨 session 管理 |
| Metrics | pipeline/metrics.py | 可选指标采集 |
| GeoResolver | proxy/geo.py | 可选 IP 地理定位 |
| Events | core/events.py | 可选异步事件系统 |
| Detectors | decision/detectors.py | 检测器（DecisionEngine 未用） |
| WasmParser | anti_font/wasm_parser.py | WASM 二进制解析（wasm_interceptor 未用） |

---

## ❌ 方案文档中的待办 (非代码层)

| 文档 | 待推进 | 工作量 |
|------|--------|--------|
| 隐蔽性提升方案 | WebGPU 拦截 (C++), Storage API 注入, Permissions API 注入 | 7-10 天 |
| 隐蔽性提升方案 | WASM SIMD → 标量补丁 (C++) | 7-10 天 |
| 数据提取效率 | Prompt 工程 + Few-Shot + CoT | 2 天 |
| 数据提取效率 | HTTP/2 帧指纹修复 | 3 天 |
| 数据提取效率 | 内容哈希去重 | 1 天 |
| 最终版方案 | 三级决策 L1/L2 (小模型+API) | 3-5 天 |
| 最终版方案 | 真实 GPU 直通替代 SwiftShader | 硬件 |
| 最终版方案 | 代理池 24h 冷却 + 预热养号 | 2 天 |

---

## 🐛 已知遗留 Bug

| 问题 | 文件 | 严重度 |
|------|------|--------|
| fingerprint/consistency.py 仅 2 个 DeviceProfile | consistency.py | P0 |
| fingerprint 6 层指纹无实际执行层 (TLS/Canvas/WebGL/Audio) | consistency.py | P0 |
| pipeline/core.py asyncio.TimeoutError 重试逻辑 | core.py | P1 |
| passive_signals MouseIdlePattern 绝对坐标 | passive_signals.py | P0→已修复 |
| weighter.py 未使用的 total 变量 | weighter.py | P2 |

---

## 📊 代码统计

```
86 个 .py 文件
~10,000+ 行代码
30 个 pipeline/core 模块
14 个孤立模块（4 核心 + 10 可选）
0 个未追踪的 git 更改
```

---

## 🚀 启动命令

```bash
cd /Users/songmoxin/WorkBuddy/2026-05-21-task-1

# Web 可视化面板
/usr/bin/python3 -m apexcrawler.cli.main dashboard

# 自然语言抓取
/usr/bin/python3 -m apexcrawler.cli.main ask "抓取 huaspeed.cc 的价格"

# CLI 爬取
/usr/bin/python3 -m apexcrawler.cli.main crawl --url https://example.com
```

**注意：必须用系统 Python (`/usr/bin/python3`)，managed Python 有签名问题。**
