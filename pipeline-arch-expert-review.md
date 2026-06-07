# Python 架构与管线模块审查报告

**审查时间**: 2026-06-06  
**审查范围**: pipeline/ · cli/ · core/ · engine/ · proxy/ · decision/ · plugin/ · routing/ · extraction/ · tests/  
**审查版本**: apexcrawler v0.1.0

---

## 总体评分: 6.8 / 10

评分细目：
| 维度 | 评分 | 说明 |
|------|------|------|
| **管线完整性** | 8/10 | 6 阶段管线完整实现，Schedule→Route→Evade→Extract→Validate→Store 全部串联 |
| **CLI 集成** | 8/10 | CLI `crawl` 和 `ask` 命令完整执行管线，不再是 stub |
| **EnginePool 集成** | 7/10 | EnginePool 正确实现，但 CLI 传递 `conn_manager=None` 导致 StealthProxy 不可用 |
| **DecisionEngine** | 7/10 | L0 到 L2 三级决策框架存在，但 L1 小模型接口未与 CLI 集成 |
| **插件系统** | 8/10 | 4 生命周期钩子 + PluginManager 完整实现 |
| **Dashboard** | 7/10 | FastAPI 内嵌实现，功能可用但仅 HTTP 层提取 |
| **BatchPipeline** | 3/10 | batch.py 存在，`batch run` 命令未实现 |
| **测试覆盖** | 5/10 | 仅 3 个测试文件共 ~18 个测试，覆盖率低 |
| **并发安全** | 7/10 | async+Semaphore 良好，但健康检查循环有潜在资源泄漏 |
| **异常体系** | 8/10 | 三层异常体系完整（Retryable/NonRetryable/Fatal） |
| **代码质量** | 6/10 | 存在重复代码、硬编码、潜在 bug |

---

## 各文件审查

### 1. pipeline/ 管线模块

#### 1.1 `pipeline/core.py` — PipelineExecutor

✅ **正确性**: PipelineExecutor.run() 正确依次迭代 stages 列表并调用 `stage.execute(ctx)`。使用 `RetryPolicy` 针对失败 stage 重试。提供 `rollback_on_failure` 选项，支持全部或单个 stage 回滚。

⚠️ **问题**:
- **重复定义** — `RetryPolicy` 在 `core.py` 和 `stages.py` 各定义一次，违反 DRY 原则。应从 `core.py` 引用。
- **`_should_retry` 逻辑** — 按 stage 名称判断，但 `configs` 字典 keys 与 stage.name 可能不匹配（如 `"extract"` vs `"ExtractStage"`），导致重试策略不生效。
- **`run()` 返回值类型不明确** — 返回 `tuple[bool, PipelineContext]`，但 `bool` 仅指示整体成功/失败，与 `ctx.fatal_error` 语义重叠不一致。

#### 1.2 `pipeline/stages.py` — 6 个 stage

✅ **ScheduleStage**: 实现 `compute_delay()` + 定时控制器集成，正确。

⚠️ **RouteStage**: 
- `_route_by_domain()` — 对未知域名恒返回 `"vanilla"`，未调用 `EngineMatcher` 做智能匹配。
- 硬编码域名映射表（amazon/cloaked, taobao/cloaked）难以维护。应改用 `EngineMatcher.best_engine()`。

⚠️ **EvadeStage**: 
- 构造函数接受 `proxies: list[str]` 但未集成 `ProxyPool`。如果未传入代理，绕过全部 stealth 逻辑直接返回 `ctx`。
- `TLSRouter` 生成 TLS Profile 后在 `_select_profile()` 中未被真正使用（未将 profile 注入到实际 HTTP 请求中）。

⚠️ **ExtractStage**:
- CLI 中传递 `conn_manager=None` → `StealthProxy` 从未被使用，导致 Playwright 直接通过系统网络连接，暴露真实的浏览器指纹和连接池行为。
- `engine_factory` 仅在 `_use_browser()` 中用到，但 `_use_browser()` 的逻辑不够明确。

⚠️ **FontDecodeStage**: 
- 仅在 `stages` 列表中声明，但实际 `decode_font()` 方法是 `@staticmethod` 返回空字符串 `""`，未实现实际的字体解码逻辑。

✅ **StoreStage**: 生成 16 位 `stored_id` 逻辑正确。使用 `uuid.uuid4().hex[:16]`，碰撞概率极低。

#### 1.3 `pipeline/batch.py` — BatchPipelineExecutor

❌ **重大问题**: 
- `BatchPipelineExecutor` 有 `__init__` 和类定义，但 `batch run` CLI 命令未实现。
- CLI 的 `--batch` 选项只是按顺序逐个调用 `PipelineExecutor.run()`，完全不使用 `BatchPipelineExecutor`。
- 无并行控制、无速率限制、无结果聚合。

#### 1.4 `pipeline/parallel.py`

✅ 结构合理，使用 `asyncio.Semaphore` 控制并行度。但此文件未被任何上游模块引用（CLI 未使用、BatchPipelineExecutor 未使用）。

#### 1.5 `pipeline/rate_controller.py` — 6 级速率控制器

✅ **优秀**: 实现完整，信号驱动速率升降（429→升级，时序恢复→降级）。6 级速率 `[5, 2, 1, 0.5, 0.1, 0.05]` 合理。

⚠️ **问题**: `_maybe_recover()` 在 `signal_success()` 和 `get_delay()` 中同时调用，且内部增量逻辑不一致（`get_delay()` 中可能越级恢复）。修复建议：统一使用 `signal_success()` 作为恢复信号入口。

#### 1.6 `pipeline/session_manager.py` — 会话管理器

✅ 良好的域名隔离实现，`ensure_consistency()` 防止引擎切换。

⚠️ **问题**: `cleanup_stale()` 使用 `time.monotonic()` 判断过期，但 `created_at` 也由 `time.monotonic()` 生成，一致。但未在 PipelineExecutor 中定期调用，可能导致内存泄漏累积。

#### 1.7 `pipeline/degrade.py` — 降级链

✅ **API → HTTP → Browser 三级降级** 框架完整。`should_use_browser()` 检测 captcha/403/429/空响应，逻辑正确。

⚠️ **问题**: 
- `degrade()` 方法中的 `_DEGRADE_CHAIN` 包含 `"api"` → `"vanilla"`，但 `"api"` 不是已注册的 engine 名称，会导致 `EngineRegistry.get("api")` 返回 None。
- `record_response()` 和 `record_failure()` 两条路径独立累加失败次数字段，可能造成计数翻倍。

#### 1.8 `pipeline/metrics.py` — 可观测性

✅ MetricsCollector 和 AlertRules 实现完整，支持 P50/P95/P99 计算。

⚠️ **问题**: 未被 PipelineExecutor 集成——PipelineExecutor 中没有任何对 `MetricsCollector` 的调用。

### 2. cli/ 命令模块

#### 2.1 `cli/main.py` — CLI 入口

✅ **关键确认: CLI `crawl` 命令已不再是 stub**。它完整构建了 PipelineExecutor 并执行 `await executor.run(ctx)`。

⚠️ **存在的问题**:

1. **重复的系统代理清除代码** — `for _k in [...]` 循环出现两次（L169-L170 和 L215-L216），第二次是冗余的。
2. **双重检查** — `if not ctx_obj.selected_engine: ctx_obj.selected_engine = "vanilla"` 也出现两次。
3. **`conn_manager=None`** — `ExtractStage(engine_factory=engine_pool, conn_manager=None)` 传入了 `None`，意味着 StealthProxy（aiohttp 代理层）不可用。这是一个重要的安全缺陷，因为 Playwright 会直接暴露网络连接。需要预先启动 `ConnectionReuseManager` 并传入。
4. **`ask` 命令中的 `_run()` 函数** — 未设置 `settings` 参数 (`PipelineExecutor(stages, configs, settings=None)`)，导致配置验证回溯。同时 `ask` 命令中的 `PipelineContext` 被创建两次（一次在函数开头，一次在 Phase 2 开始前），第一次创建的 ctx_obj 被第二次覆盖，浪费计算。
5. **SSRF 校验函数 `_validate_url` 的 DNS 解析安全** — 解析后的 IP 仅在校验时使用，CLI 实际请求时未使用该 IP。
6. **`dashboard` 命令** — 嵌入的 FastAPI 应用功能有限：`/api/ask` 仅调用 `_try_http_extract()`，未运行完整管线。用户期望 dashboard 使用完整的 ApexCrawler 管线，而不是简单的 HTTP 提取。

### 3. core/ 核心模块

#### 3.1 `core/context.py` — PipelineContext

✅ 设计良好。所有 6 个 stage 的输入/输出字段明确分离，有注释说明每个字段属于哪个 stage。

⚠️ **问题**: `webgl_renderer`, `canvas_hash`, `audio_fingerprint`, `fonts` 字段重复定义（L38-L40 和 L51-L54）。这是 Python dataclass 的 bug —— 后定义的同名字段会覆盖前定义，但 `field(default_factory=list)` 的默认工厂不会丢失。修复：删除冗余定义。

#### 3.2 `core/events.py` — 事件总线

✅ 事件类型定义清晰。但 **事件总线未实现** —— 只有事件 dataclass，没有 `EventBus` publish/subscribe 机制。

#### 3.3 `core/exceptions.py` — 异常体系

✅ **优秀**: 三层异常体系：
- `RetryableError` → ProxyError, RateLimitError, EngineError, ExtractionError, CaptchaDetected
- `NonRetryableError` → ConfigurationError, SchemaValidationError, NotSupportedError
- `FatalError` → AntiCrawlDetected, EnginePoolExhausted

层次分明，覆盖了所有关键失败场景。

#### 3.4 `core/protocols.py` — Protocol 接口

✅ 使用 `typing.Protocol` 定义抽象接口（Engine, Page, ProxyProvider, Extract，PipelineStage, DecisionEngine），支持鸭子类型。良好。

### 4. engine/ 引擎模块

#### 4.1 `engines/base.py` — BaseEngine + EngineCapability

✅ 设计良好。ABC + `EngineCapability` dataclass 提供 10 分制评分体系。

#### 4.2 `engines/pool.py` — EnginePool

✅ **已完成整合**:
- 使用 `EngineRegistry` 发现已注册引擎
- `acquire()` 上下文管理器使用双 Semaphore 控制并发（全局 + 按引擎类型）
- `close_all()` 正确清理所有实例
- 30s 超时保护防止死锁

⚠️ **问题**: `acquire()` 在 `finally` 中始终调用 `await engine_instance.close()`，**即使 yield 成功返回也关闭**。这意味着每次 `acquire()` 只能使用一次 engine 实例，之后立即关闭。这完全破坏了「池化」的语义 —— 池化应该保持实例存活以便重用。这是一个**严重 bug**。

#### 4.3 `engines/vanilla.py`, `cloaked.py`, `camouflaged.py`, `patched.py`

✅ **4 个引擎全部实现并通过 `@EngineRegistry.register` 注册**：
- **Vanilla** (Playwright Chromium) — fingerprint_resistance=2
- **Cloaked** (CloakBrowser) — fingerprint_resistance=10
- **Camoufox** (Camoufox Firefox) — fingerprint_resistance=8
- **Patched** (Playwright + stealth JS) — fingerprint_resistance=5

⚠️ **问题**:
- **Camoufox 使用的 Playwright firefox.launch 错误** — Camoufox 是一个 Firefox *fork*，应设置 `channel` 或通过 `executable_path` 启动。当前仅使用了标准的 `firefox.launch()`，没有区分 Camoufox 二进制文件。
- **Cloaked 中 `navigate()` 可能多次调用 `launch()`** — 如果 `self._page` 为空，会调用 `launch()`，但 `launch()` 没有 `async` 保护（没有 `_launched` 标志），可能重复启动。
- **`_PageAdapter` 在 4 个文件中重复定义** — 至少有 4 次几乎相同的实现。应提取到公共模块。

### 5. proxy/ 代理模块

#### 5.1 `proxy/pool.py` — ProxyPool

✅ **优秀实现**:
- 实现 `ProxyProvider` protocol
- 4 种轮转策略（RoundRobin/Random/Weighted/LeastUsed）
- 并发健康检查 + 冷却期
- 指数移动平均延迟
- 地理标签过滤

⚠️ **问题**:
- `stats()` 中 `geo_distribution` 实现有误，使用 `defaultdict(int, ...)` 但初始化的 dict 中所有值都是 `int` 而非 `count`。
- 健康检查使用 `https://httpbin.org/ip`，硬编码依赖，且 httpbin.org 在某些地区不可用。

#### 5.2 `proxy/geo.py` — GeoIP 匹配

✅ 完整实现：支持 MaxMind / ip-api.com / ipinfo.io 三个后端，带 LRU 缓存和批量查询。

#### 5.3 `proxy/self_healer.py` — 代理自愈

✅ 3 级切换：同地域→跨地域→告警。逻辑清晰。

⚠️ **问题**: `get_healthy_proxy()` 先调用 `pool.get_proxy()` 再独立调用 `health_check()`，导致重复消耗。应利用 pool 自身的健康状态标记。

### 6. decision/ 决策引擎

#### 6.1 `decision/engine.py` — DecisionEngine

✅ **三级决策框架完整**：
- **L0**: 内存缓存，5 分钟 TTL
- **L1**: 本地 Ollama 模型，根据域名预测难度和引擎（`_run_l1()`）
- **L2**: 默认规则返回 `{"entry_point": "http", "use_browser": False}`

✅ **反爬厂商检测** — 8 个厂商签名（Cloudflare, Akamai, DataDome, PerimeterX, Kasada, F5, Imperva, Distil），从 header/cookie/HTML 三方检测，准确度高。

⚠️ **问题**:
- **L1 模型未集成到任何实际调用路径** — `DecisionEngine` 仅在 `cli/main.py` 的 `ask` 命令中被引用，但 `PipelineExecutor` 和 `RouteStage` 不使用它。`recommend()` 方法只有在外部显式调用时才会生效。
- **`_recommend_engine()` 硬编码映射** — 应使用 `EngineMatcher` 动态匹配而非硬编码。

#### 6.2 `decision/detectors.py` — 信号检测器

✅ **优秀**: 5 个独立检测器（Captcha / WAF / JSChallenge / Fingerprint / Honeypot）+ 组合器。正则签名覆盖全面，包含 reCAPTCHA、hCaptcha、Cloudflare Turnstile、GeeTest、Arkose 等。

### 7. plugin/ 插件系统

#### 7.1 `plugins/__init__.py` — Plugin + PluginManager

✅ **完整实现 4 生命周期钩子**：
- `on_pre_schedule` — 调度前
- `on_post_extract` — 提取后
- `on_pre_store` — 存储前
- `on_error` — 错误时

✅ `PluginManager.dispatch()` 异常隔离 —— 插件失败不会影响管线。正确。

#### 7.2 `plugins/builtin.py` — 3 个内置插件

✅ LoggingPlugin（日志）、JSONExportPlugin（JSON 导出）、RetryAlertPlugin（重试告警）。实现完整实用。

⚠️ **PipelineExecutor 中未使用 `on_pre_schedule` 和 `on_pre_store` 钩子** — 当前仅调用了 `on_post_extract` 和 `on_error`。需要补充。

### 8. extraction/ 提取模块

#### 8.1 `extraction/ai_extractor.py` — AIExtractor

✅ 三层提取链路：JSON-LD → HTML Meta → LLM API。带智能截断和缓存。

⚠️ **问题**: `_try_llm()` 中直接读取 `settings.llm.api_key` 并以明文传入 HTTP 请求。应使用 `get_secret_value()` 避免日志泄漏。

#### 8.2 `extraction/schema.py` — Pydantic Schema

✅ 7 个预置 Schema（Product, Article, SearchResult/Page, Review/Page, Company, GenericEntity）+ `get_schema()` 注册表。覆盖常见爬取场景。

#### 8.3 `extraction/cleaner.py` — 数据清洗

✅ 全面的清洗工具：文本清洗、价格提取、日期解析、URL 净化。价格提取支持多国货币符号和 ¥(CNY/JPY) 消歧。

#### 8.4 `extraction/cross_validator.py` — 交叉验证

✅ 多源投票验证（JSON-LD + Microdata + OpenGraph + Meta + LLM），使用 Jaccard 相似度聚类。架构良好。

### 9. routing/ 路由模块

#### 9.1 `routing/registry.py` — EngineRegistry

✅ 装饰器注册模式，`EngineRegistry.register` 自动收集引擎类。`list_capabilities()` 返回能力描述。

#### 9.2 `routing/matcher.py` — EngineMatcher

✅ 权重评分引擎匹配，根据难度动态调整权重（高难度优先 stealth）。实现完备。

⚠️ **未与 RouteStage 集成** — `EngineMatcher` 完全未被 `RouteStage` 使用。

### 10. 测试覆盖

#### `tests/test_pipeline.py` — 9 个基本测试
#### `tests/test_pipeline_integration.py` — 5 个集成测试
#### `tests/test_extraction.py` — 7 个提取测试

**总数: 3 个文件, ~21 个测试用例**

⚠️ **严重的测试不足**:
- **CLI 测试: 0** — 没有测试 click 命令解析、SSRF 校验、`_validate_url()`、批量爬取。
- **EnginePool 测试: 1** — 仅测试导入，未测试 `acquire()`/`close_all()`/并发控制/超时。
- **Plugins 测试: 0** — 没有测试 PluginManager 注册/派发/钩子触发。
- **Dashboard 测试: 0**
- **DecisionEngine 测试: 0**
- **ProxyPool 测试: 0**
- **RateController 测试: 0**（基本功能在 `test_pipeline.py` 中测试）
- **异常路径测试: 0** — 没有测试 ProxyError/EngineError/RetryableError 的重试逻辑。

---

## 关键发现（按严重度排序）

### 🔴 严重 (P0)

1. **`EnginePool.acquire()` 立即关闭 engine 实例（`pool.py` L102）**
   - 问题：finally 块中无条件调用 `engine_instance.close()`，导致 pool 完全丧失复用能力。
   - 影响：每次 `acquire()` 创建并销毁 engine，相当于无池化。
   - 修复：在 finally 中只释放信号量（semaphore），不关闭 engine。将 engine 实例放回空闲队列供复用。

2. **`PipelineContext` 字段重复定义（`core/context.py` L38-L40 vs L51-L54）**
   - 问题：`webgl_renderer`, `canvas_hash`, `audio_fingerprint`, `fonts` 被定义两次。
   - 影响：在 Python dataclass 中，后定义的同名字段覆盖先定义的（包括其 `field()` 参数）。`fonts: list[str] = field(default_factory=list)` 可能被非工厂版本覆盖，导致不可预期的默认值行为。
   - 修复：删除第一次定义（L38-L40）。

### 🟠 高 (P1)

3. **`PipelineExecutor` 未集成 MetricsCollector**
   - 影响：管线运行时无任何指标收集，无法监控成功率、延迟、降级率。

4. **CLI `crawl` 传递 `conn_manager=None`，StealthProxy 不可用**
   - 影响：Playwright 绕过本地代理直接连接目标，暴露真实的连接池行为和浏览器指纹。

5. **RouteStage 未使用 EngineMatcher 和 DecisionEngine**
   - 影响：路由决策退化为简单的域名硬编码表。当托管模式切换（如目标从低难度变为高难度），无法自适应选择引擎。

6. **`BatchPipelineExecutor` 和 `parallel.py` 未集成**
   - 影响：`--batch` 模式串行执行，无并行控制、无批量速率控制、无结果聚合。

### 🟡 中 (P2)

7. **CLI `ask` 命令 PipelineContext 双重创建**
   - 影响：第一个 `ctx_obj` 被创建后立即丢弃，浪费 trace_id。

8. **CamoufoxEngine 未正确使用 Camoufox 二进制**
   - 当前使用标准的 `firefox.launch()`，没有区分 Camoufox 的 Firefox fork。

9. **`FontDecodeStage.decode_font()` 未实现**
   - 返回硬编码空字符串，反爬字体解码管线不可用。

10. **`RateController._maybe_recover()` 并发不安全**
    - `get_delay()` 和 `signal_success()` 可能同时修改 `_recovery_counter`。

11. **`DegradeManager.record_response()` 和 `record_failure()` 路径重复计数**
    - 同时调用导致失败计数翻倍，导致过早降级。

### 🔵 低 (P3)

12. **`RetryPolicy` 在 `core.py` 和 `stages.py` 重复定义**
13. **`_PageAdapter` 在 4 个引擎中重复实现**
14. **`EvadeStage` 未真正注入 TLS Profile 到 HTTP 请求**
15. **ProxyPool 健康检查 URL 硬编码 httpbin.org**
16. **Dashboard `/api/ask` 未使用完整管线**
17. **Plugin 的 `on_pre_schedule` 和 `on_pre_store` 钩子在 PipelineExecutor 中未被调用**

---

## 管线执行完整性确认

### CLI `crawl` 命令管线流程

```
├── 验证URL (SSRF校验) ✅
├── 创建 PipelineContext ✅
├── 清除系统代理 ✅
├── 构建 stages [Schedule, Route, Evade, Extract, Validate, FontDecode, Store]
├── PipelineExecutor.run(ctx)
│   ├── ScheduleStage.execute()     ✅ (计算延迟)
│   ├── RouteStage.execute()        ✅ (选择引擎)
│   ├── EvadeStage.execute()        ⚠️ (TLS注入未生效)
│   ├── ExtractStage.execute()      ⚠️ (conn_manager=None, StealthProxy未用)
│   ├── ValidateStage.execute()     ✅
│   ├── FontDecodeStage.execute()   ❌ (未实现)
│   └── StoreStage.execute()        ✅ (生成stored_id)
└── engine_pool.close_all()         ⚠️ (pool.acquire()关闭早于此)
```

**结论**: 管线完整执行，但 ExtractStage 的代理层和 FontDecodeStage 存在问题。

### 6 阶段管线完整性

| 阶段 | 状态 | 说明 |
|------|------|------|
| Schedule | ✅ 完成 | 含人机定时控制、内容停留时间、疲劳间隔 |
| Route | ⚠️ 部分完成 | 未使用 EngineMatcher/DecisionEngine，仅硬编码映射 |
| Evade | ⚠️ 部分完成 | 代理传递到 TLSRouter 但未注入 HTTP；fingerprint 注入在 engine 层而非 EvadeStage |
| Extract | ⚠️ 部分完成 | 引擎执行正确，但 StealthProxy 不可用 |
| Validate | ✅ 完成 | 三种校验（HTML大小、结构化、Schema类型） |
| Store | ⚠️ 框架完成 | 仅生成 stored_id，无实际持久化存储 |

### DecisionEngine 三级决策完整性

| 层级 | 实现 | 集成情况 |
|------|------|----------|
| L0 (规则) | ✅ VENDOR_SIGNATURES + SWITCH_SIGNALS + 内存缓存 | ⚠️ 仅 `decision/engine.py` 可用，PipelineExecutor 未调用 |
| L1 (小模型) | ✅ `_run_l1()` 调用本地 Ollama | ❌ 无集成点，无测试 |
| L2 (API/默认) | ✅ 返回基础策略 | ✅ CLI 中 fallback 使用 |

### 插件系统完整性

| 钩子 | 实现 | 在 PipelineExecutor 中调用 |
|------|------|---------------------------|
| `on_pre_schedule` | ✅ Plugin.`` | ❌ 未被调用 |
| `on_post_extract` | ✅ LoggingPlugin 使用 | ✅ `pipeline/core.py` L155 |
| `on_pre_store` | ✅ JSONExportPlugin 使用 | ❌ 未被调用 |
| `on_error` | ✅ RetryAlertPlugin 使用 | ✅ `pipeline/core.py` L148 |

### 并发安全与资源泄漏

**良好**:
- `asyncio.Lock` 保护 ProxyPool 和 SessionManager 的共享状态
- `asyncio.Semaphore` 控制 EnginePool 并发
- `atomic write`（tempfile + rename）保护 CookieJarStore

**风险**:
1. **EnginePool 每次 acquire 创建新实例** — 资源重复创建/销毁
2. **SessionManager 会话无 GC** — `cleanup_stale()` 从未被调用
3. **ProxyPool 健康检查协程** — `start_health_checks()` 在 `_loop()` 中无限运行，无法被外部关闭（`stop_health_checks()` 可取消但未在生命周期管理中调用）
4. **Dashboard FastAPI 应用** — 未设置请求超时和并发限制

---

## 总结

ApexCrawler 的核心管线架构设计良好，定义了清晰的 6 阶段管线、三级决策引擎、4 引擎池化、三层异常体系、4 钩子插件系统和全面的反爬检测器集。CLI 命令（`crawl`, `ask`, `dashboard`, `template`, `visual`, `config`）丰富且功能完整。

**当前最大问题**: `EnginePool.acquire()` 在每个请求后立即关闭 engine 实例，破坏了整个池化设计的核心假设。修复此问题应为首要任务。

**次大问题**: 各模块之间的集成断点。`PipelineContext` 虽然作为共享状态穿行管线，但 `RouteStage` 不使用 `EngineMatcher/DecisionEngine`，`PipelineExecutor` 不使用 `MetricsCollector`，`BatchPipelineExecutor` 未集成到 CLI。

**测试严重不足**: 仅 ~21 个测试，覆盖不到核心模块的 20%。无 CLI 测试、无 DecisionEngine 测试、无 ProxyPool 测试、无 Plugin 测试。

**总体而言**: 架构优秀，实现有实质进展，但存在影响正确性和鲁棒性的关键 bug。需要优先修复 `EnginePool.acquire()` 和 PipelineContext 重复字段 bug，然后补全集成断点和测试覆盖。
