# ApexCrawler — 可自行实现 vs 需外部技术

> 分类标准：纯 Python / CDP 层面 = ✅ 自己做 | 需 C++ 源码修改 / 硬件 = ❌ 外部补上

---

## ✅ 可自行实现（纯 Python，共 42 项）

### 🐛 Bug 修复（5 项）

| # | 问题 | 文件 | 可行原因 |
|---|------|------|---------|
| 1 | DeviceProfile 仅 2 个 | `fingerprint/consistency.py` | 纯 dataclass，加几个 Profile |
| 2 | 6 层指纹无执行层 | `fingerprint/consistency.py` | CDP `addInitScript` 注入 JS 覆盖 navigator/Canvas/WebGL/Audio |
| 3 | TimeoutError 回滚行为 | `pipeline/core.py` | 纯逻辑修改 |
| 4 | ExtractStage 不回退 JSON-LD | `extraction/ai_extractor.py` | 纯 Python 逻辑 |
| 5 | LLM 调用链未接通 | `extraction/ai_extractor.py` | 加 `_try_llm` 方法 |

### ⚠️ 代码已存在但未接入（4 核心 + 10 可选）

| # | 模块 | 文件 | 接入方式 |
|---|------|------|---------|
| 6 | FontCracker + OCREngine | `anti_font/*.py` (1,208 行) | 新建 FontDecodeStage |
| 7 | SelHealer | `extraction/sel_healer.py` (54 行) | ExtractStage 失败时调用 |
| 8 | Cleaner | `extraction/cleaner.py` (303 行) | ValidateStage 后调用 |
| 9 | BrotliSupport | `utils/brotli_support.py` (28 行) | httpx client 集成 |
| 10 | SessionManager | `pipeline/session_manager.py` (117 行) | PipelineExecutor 初始化 |
| 11 | RateController | `pipeline/rate_controller.py` (117 行) | ScheduleStage 后 |
| 12 | Metrics | `pipeline/metrics.py` (157 行) | PipelineExecutor 全局 |
| 13 | DegradeManager | `pipeline/degrade.py` (141 行) | ExtractStage 降级链 |
| 14 | ParallelZone | `pipeline/parallel.py` (33 行) | Evade+Extract 并行 |
| 15 | AdaptiveRecovery | `pipeline/adaptive_recovery.py` (51 行) | 全局异常恢复 |

### 🆕 P0 新功能（9 项）

| # | 任务 | 实现方式 | 工作量 |
|---|------|---------|--------|
| 16 | Prompt 升级（Few-Shot + CoT + JSON mode） | 改 `_build_prompt` 字符串 + LLM 参数 | 2 天 |
| 17 | 智能 HTML 裁剪（语义分块） | BeautifulSoup 解析 + 优先级排序 | 1 天 |
| 18 | 结构化数据优先（JSON-LD → LLM） | extruct 库解析 + 优先级回退 | 1 天 |
| 19 | 子资源完整加载 | Playwright `page.route()` + CDP Network 域 | 2 天 |
| 20 | TCP 连接复用管理 | aiohttp TCPConnector，已有 164 行框架 | 1 天 |
| 21 | 多引擎切换信号增强 | 正则匹配 403/503/captcha/空响应 | 1 天 |
| 22 | 字体反爬决策矩阵 | 字典映射 (font_type, encoding) → strategy | 1 天 |
| 23 | 内容哈希去重 | `hashlib.sha256(html)` → LRU cache | 1 天 |
| 24 | FontDecodeStage 接入 pipeline | 新建 stage + 注册到管线 | 1 天 |

### 🆕 P1 加固（9 项）

| # | 任务 | 实现方式 | 工作量 |
|---|------|---------|--------|
| 25 | 指纹一致性闭环 | CDP `addInitScript` 注入 6 层 JS 覆盖 | 3 天 |
| 26 | DeviceProfile 扩展至 5+ | 加 win_firefox / mac_chrome / mac_safari | 0.5 天 |
| 27 | 跨页面行为持久化 | SessionContext 对象挂载到 PipelineContext | 1 天 |
| 28 | 自愈 XPath（5 种冗余选择器） | ID → class → text → data-attr → semantic | 1 天 |
| 29 | 多源交叉验证 | JSON-LD + meta + microdata + LLM 投票 | 1 天 |
| 30 | HTTP/2 SETTINGS 帧注入 | aiohttp proxy 层改 SETTINGS 参数 | 2 天 |
| 31 | DegradeManager 集成 | API→HTTP→Browser 三层 fallback | 1 天 |
| 32 | TLS Session 缓存 | 内存 dict 存 session ticket | 0.5 天 |
| 33 | sendBeacon 拦截 | CDP Network.requestWillBeSent 监听 | 1 天 |

### 🆕 P2 收尾（5 项）

| # | 任务 | 实现方式 | 工作量 |
|---|------|---------|--------|
| 34 | 移动端 API 嗅探增强 | URL 变换 + 并行 HEAD 探测 | 1 天 |
| 35 | L1 本地小模型 | Ollama / transformers 本地推理 | 3 天 |
| 36 | 三级决策 L1/L2 | L0 规则 + L1 小模型 + L2 API | 3 天 |
| 37 | DNS 预取模拟 | CDP 注入 `<link rel="dns-prefetch">` | 1 天 |
| 38 | 代理池 24h 冷却 + 预热 | ProxyPool 加 cooldown_until 字段 | 2 天 |

---

## ✅ 纠正：以下 5 项全部已有代码实现（CDP JS 注入层）

> 之前误分类为"需外部 C++"，实际代码里全都有了——通过 `addInitScript` JS 注入解决。

| # | 项目 | 已有文件 | 行数 | 实现方式 |
|---|------|---------|------|---------|
| C1 | **WASM SIMD 拦截** | `anti_font/wasm_interceptor.py` | 283 行 | CDP 注入 JS：拦截 `WebAssembly.instantiate/compile/streaming`，解析 WASM 二进制检测 SIMD opcode (0xFD) 并阻断 |
| C2 | **WebGPU 拦截** | `anti_font/wasm_interceptor.py` L141-152 | 12 行 | 拦截 `navigator.gpu.requestAdapter` 强制 low-power fallback |
| C3 | **GPU 指纹库** | `anti_font/wasm_interceptor.py` L208-239 | 32 行 | 5 个真实 GPU 指纹 (RTX3060/4070/Intel UHD/AMD RX6600/Apple M2) |
| C4 | **Storage API** | `pipeline/storage_store.py` | 47 行 | LocalStorage + SessionStorage 捕获/恢复，跨 session 持久化 |
| C5 | **Permissions API** | `engines/patched.py` | — | `add_init_script` 覆盖 `navigator.permissions.query`，修复 notifications 权限 |

### ⚠️ 仅 1 项需硬件支持

| 项目 | 现状 | 备注 |
|------|------|------|
| **真实 GPU 直通** | 已有 fallback（GPU 指纹库 + Docker 指南） | 真 GPU 硬件需外部配置，但已有 5 个真实指纹做软件模拟 |

### 结论

```
✅ 纯 Python 可自行完成: 47 项（100%）
⚠️ 仅需硬件:           1 项（GPU 直通，已有 fallback）
❌ 需 C++ 源码修改:     0 项
```

---

## 📊 汇总

| 分类 | 数量 | 工作量 |
|------|------|--------|
| ✅ 我们自己能做（Python） | **42 项** | ~24 天 |
| ❌ 外部技术补上（C++/硬件） | **5 项** | ~15-26 天 |

**比例**：我们能做到 **89%**（42/47），只有 11% 需要 C++ 大神 / GPU 硬件。

---

## 🎯 推荐的我们自己动手顺序

```
第一波（3-4 天）：Bug 修复 + 模块接入
  ✅ 修复 consistency.py（+3 Profile + CDP 注入）
  ✅ FontDecodeStage 接入 pipeline
  ✅ SelHealer + Cleaner + BrotliSupport 全部接入
  ✅ DegradeManager + SessionManager 接入
  ✅ 修复 LLM 调用链

第二波（5-7 天）：P0 新功能
  ✅ Prompt 升级 + HTML 智能裁剪 + 结构化优先
  ✅ 子资源完整加载 + 连接复用管理
  ✅ 字体决策矩阵 + 内容哈希去重 + 引擎切换增强

第三波（5-7 天）：P1 加固
  ✅ 指纹闭环（DeviceProfile → 6 层 CDP 注入）
  ✅ 自愈 XPath + 多源交叉验证
  ✅ HTTP/2 SETTINGS 帧注入
  ✅ 跨页面行为 + TLS 缓存 + sendBeacon

第四波（5-7 天）：P2 收尾
  ✅ L1 小模型 + 三级决策
  ✅ 移动 API 增强 + DNS 预取 + 代理池冷却
```

---

## 🔄 外部技术分工建议

等我们自己做完上面 42 项，C++ 部分这样安排：

| 项目 | 建议来源 |
|------|---------|
| WASM SIMD 补丁 | 找 Chromium/V8 contributor，这类改 turbofan 的 patch 一般在 chromium-review 上能找到参考 |
| WebGPU 拦截 | 参考 CloakBrowser 的补丁模式（它已在 content/renderer 做了 49 个补丁），扩展到 WebGPU |
| GPU 直通 | Docker `--gpus all` + NVIDIA Container Toolkit，或直接用云 GPU 实例 |
| Storage/Permissions API | 这两个相对简单，CloakBrowser 的 Blink 补丁模式可以直接套用 |
