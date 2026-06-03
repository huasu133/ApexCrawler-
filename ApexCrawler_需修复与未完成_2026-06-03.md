# ApexCrawler 需修复 & 未完成 — 2026-06-03

> 交叉核查：5 份方案文档 × 本地代码 (86 .py / 11,024 行) × GitHub (7c50569)

---

## 🐛 Bug 需修复（代码层面）

| # | 问题 | 文件 | 行数 | 严重度 |
|---|------|------|------|--------|
| 1 | DeviceProfile 仅 2 个（缺 firefox、macOS chrome 等） | `fingerprint/consistency.py` | 59 行 | **P0** |
| 2 | 6 层指纹（TLS/Canvas/WebGL/Audio/JS navigator/HTTP headers）仅做 hash，无 CDP 注入执行层 | `fingerprint/consistency.py` | 全文 | **P0** |
| 3 | pipeline TimeoutError 重试耗尽后抛 RetryableError 导致整条 pipeline 回滚 | `pipeline/core.py` | :94 | P1 |
| 4 | ExtractStage 失败不回退 JSON-LD/Microdata | `extraction/ai_extractor.py` | — | P1 |
| 5 | ExtractStage 占位实现（`_try_llm` 方法缺失，LLM 调用链未接通） | `extraction/ai_extractor.py` | 149 行 | **P0** |

---

## ⚠️ 代码已存在但未接入 Pipeline

| # | 模块 | 文件 | 行数 | 应接入位置 |
|---|------|------|------|-----------|
| 1 | **FontCracker** + OCREngine + FontRecovery + DomFixer | `anti_font/*.py` (4 文件) | 1,208 行 | 新建 FontDecodeStage |
| 2 | **SelHealer**（选择器自愈） | `extraction/sel_healer.py` | 54 行 | ExtractStage 失败时调用 |
| 3 | **Cleaner**（数据清洗） | `extraction/cleaner.py` | 303 行 | ValidateStage 之后 |
| 4 | **BrotliSupport**（brotli 解压） | `utils/brotli_support.py` | 28 行 | httpx client 层 |

---

## 📦 未接入的独立模块（代码存在，pipeline 零引用）

| 模块 | 文件 | 行数 | 优先级 |
|------|------|------|--------|
| SessionManager | `pipeline/session_manager.py` | 117 | 🟡 中 |
| RateController | `pipeline/rate_controller.py` | 117 | 🟡 中 |
| Metrics | `pipeline/metrics.py` | 157 | 🟡 中 |
| GeoResolver | `proxy/geo.py` | 374 | 🟢 低 |
| Events | `core/events.py` | 55 | 🟢 低 |
| Detectors | `decision/detectors.py` | 428 | 🟡 中 |
| WasmParser | `anti_font/wasm_parser.py` | 260 | 🟢 低 |
| DegradeManager | `pipeline/degrade.py` | 141 | 🔴 **高** |
| ParallelZone | `pipeline/parallel.py` | 33 | 🟡 中 |
| AdaptiveRecovery | `pipeline/adaptive_recovery.py` | 51 | 🟡 中 |

---

## 🆕 方案文档要求但代码未开始

### P0（数据提取 + 隐蔽性核心）

| # | 任务 | 来源文档 | 文件 | 工作量 |
|---|------|---------|------|--------|
| 1 | **Prompt 工程升级**（Few-Shot + CoT + temperature=0 + JSON mode） | 数据提取效率 | `extraction/ai_extractor.py` | 2 天 |
| 2 | **智能 HTML 裁剪**（语义分块，保留 JSON-LD/OG/main/article） | 数据提取效率 | `extraction/ai_extractor.py` | 1 天 |
| 3 | **结构化数据优先提取**（JSON-LD → Microdata → OpenGraph → LLM） | 数据提取效率 | `extraction/ai_extractor.py` | 1 天 |
| 4 | **子资源完整加载**（`wait_until="networkidle"` + CDP entry 计数） | 隐蔽性方案 | `engines/vanilla.py` + `engines/subresource.py` | 2 天 |
| 5 | **TCP 连接复用管理**（aiohttp StealthProxy） | 隐蔽性方案 | `http/connection_pool.py` 已有 164 行，需集成 | 1 天 |
| 6 | **多引擎切换信号增强**（空响应/验证码/JS挑战检测） | 数据提取效率 | `decision/engine.py` | 1 天 |
| 7 | **字体反爬决策矩阵**（FontTools vs OCR 按类型自动选择） | 数据提取效率 | `anti_font/font_cracker.py` | 1 天 |
| 8 | **内容哈希去重**（同 HTML 不重复调 LLM） | 数据提取效率 | `extraction/ai_extractor.py` | 1 天 |
| 9 | **FontDecodeStage 接入 pipeline** | 完成与待办 | `pipeline/stages.py` + 新建 stage | 1 天 |

### P1（指纹闭环 + 系统性加固）

| # | 任务 | 来源文档 | 工作量 |
|---|------|---------|--------|
| 10 | **指纹全链路一致性闭环**（DeviceProfile → 6 层 CDP 注入） | 隐蔽性方案 | 3 天 |
| 11 | **DeviceProfile 扩展至 5+ 个**（firefox/macOS chrome/Safari） | 隐蔽性方案 | 0.5 天 |
| 12 | **跨页面行为持久化**（SessionContext） | 隐蔽性方案 | 1 天 |
| 13 | **自愈 XPath 重定位**（5 种冗余选择器 + 自动恢复） | 数据提取效率 | 1 天 |
| 14 | **多源交叉验证接入**（JSON-LD + LLM + microdata 投票） | 数据提取效率 | 1 天 |
| 15 | **HTTP/2 SETTINGS 帧注入**（Chrome 真实参数） | 隐蔽性方案 | 2 天 |
| 16 | **DegradeManager 集成到管线**（API→HTTP→Browser 三层） | 管线架构方案 | 1 天 |
| 17 | **TLS Session 缓存** | 数据提取效率 | 0.5 天 |
| 18 | **sendBeacon 全量拦截**（CDP Network domain 监听） | 隐蔽性方案 | 1 天 |

### P2（长期投入）

| # | 任务 | 来源文档 | 工作量 |
|---|------|---------|--------|
| 19 | **移动端 API 嗅探增强**（自动推导 API 端点） | 数据提取效率 | 1 天 |
| 20 | **L1 本地小模型**（替代部分 LLM 调用） | 数据提取效率 + 最终版方案 | 3 天 |
| 21 | **三级决策 L1/L2**（小模型 + API 接入） | 最终版方案 | 3 天 |
| 22 | **DNS 预取行为模拟** | 隐蔽性方案 | 1 天 |
| 23 | **代理池 24h 冷却 + 预热养号** | 最终版方案 | 2 天 |

---

## 🖥️ 需 C++ 源码修改（非 Python 可实现）

| # | 任务 | 工作量 | 必要性 |
|---|------|--------|--------|
| C1 | WebGPU 拦截（navigator.gpu + 4 入口） | 3-5 天 | P2 |
| C2 | Storage API 注入 | 2-3 天 | P2 |
| C3 | Permissions API 注入 | 2-3 天 | P2 |
| C4 | V8 WASM SIMD → 标量补丁（~20 处） | 7-10 天 | P1 |
| C5 | 真实 GPU 直通替代 SwiftShader | 硬件配置 | P2 |

---

## 📊 汇总

| 类别 | 数量 | 预估总工作量 |
|------|------|------------|
| 🐛 Bug | 5 项 | 2 天 |
| ⚠️ 代码未接入 | 4 项核心 + 10 项可选 | 3 天 |
| 🆕 P0 新功能 | 9 项 | 10 天 |
| 🆕 P1 新功能 | 9 项 | 11 天 |
| 🆕 P2 新功能 | 5 项 | 10 天 |
| 🖥️ C++ 补丁 | 5 项 | 15-26 天 |
| **合计** | **47 项** | **Python: 26 天 / C++: 15-26 天** |

---

## 🎯 推荐执行顺序（Python 部分）

```
第 1 周 ─ P0 Bug + P0 接入:
  ✅ 修复 consistency.py（+3 DeviceProfile + 6 层 CDP 注入）
  ✅ FontDecodeStage 接入 pipeline
  ✅ SelHealer + Cleaner 接入 ExtractStage
  ✅ BrotliSupport 接入 httpx

第 2 周 ─ P0 新功能:
  ✅ Prompt 升级（Few-Shot + CoT + JSON mode）
  ✅ 智能 HTML 裁剪
  ✅ 结构化数据优先（JSON-LD → LLM）
  ✅ 子资源完整加载
  ✅ 多引擎切换信号增强

第 3 周 ─ P1 加固:
  ✅ 指纹全链路一致性闭环
  ✅ 跨页面行为持久化
  ✅ 自愈 XPath 重定位
  ✅ DegradeManager 集成
  ✅ HTTP/2 SETTINGS 帧注入

第 4 周 ─ P2 收尾:
  ✅ 三级决策 L1/L2
  ✅ 移动端 API 增强
  ✅ DNS 预取 + sendBeacon 拦截
```
