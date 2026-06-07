# AI 提取与数据抽取模块审查报告

**审查日期**: 2026-06-06  
**审查范围**: extraction 模块、pipeline/stages.py (ExtractStage)、llm 目录  
**审查角色**: AI 提取与数据抽取专家  

---

## 总体评分: 5.5 / 10

模块展现了**优良的架构设计和降级策略思维**，但在**实际实现完整性、代码逻辑正确性、模块可达性**上存在严重问题。AIExtractor 的 `_try_llm` 已从空方法实现为包含 OpenAPI 调用的完整逻辑，但整个模块与 Pipeline 的集成存在关键断层——AIExtractor 实际上**完全没有被 ExtractStage 使用**。

---

## 各文件审查

### 1. ai_extractor.py — ⭐⭐⭐⭐⭐⭐⭐ (7/10)

**亮点**:
- ✅ `_try_llm` 已完成：包含 Settings 读取、库存结构化提取（`extract_structured`）、`smart_html_truncate` 裁剪、httpx 调用 LLM API（带 JSON mode）、结构化数据与 LLM 结果合并
- ✅ `extract` 方法实现了完善的降级链：Cache → Structured Data → LLM（`_try_llm`） → Legacy LLM Path
- ✅ `_extract_structured` 实现了 JSON-LD → OpenGraph → Twitter Card 三级降级，零 LLM cost
- ✅ `smart_html_truncate` 实现了保留 JSON-LD、title、main/article，去掉 boilterplate
- ✅ `_semantic_trim` 实现了移除 script/style/nav/footer/noscript/iframe 并截断到 max_chars
- ✅ `_build_prompt_v2` 中包含了 Few-Shot Example 和 CoT 引导（"Think step by step..."）
- ✅ Cache 存/取/写入异常处理完善

**问题**:

| # | 严重度 | 问题 | 位置 |
|---|--------|------|------|
| 1 | **Critical** | **`extract_structured` 与 `_extract_structured` 功能高度冗余**。前者返回 dict，后者返回 Pydantic 模型，但都解析 JSON-LD/OG/Meta。`_try_llm` 调用 `extract_structured`，而 `extract` 的 Step 1 调用 `_extract_structured`。两者逻辑重复，维护双倍成本。 | 多段 |
| 2 | **High** | **降级链有逻辑断点**：`extract` 方法的 Step 1 (`_extract_structured`) 调用时若成功就 return，失败后进入 Step 2 `_try_llm`。但 Step 2 内又调用了 `extract_structured`（低优先级的杂项字段提取），接着调用 LLM。Step 3 又走 `_build_prompt_v2` + `_llm.generate` 完整体质。**实际上 Step 2 和 Step 3 都调 LLM，只是 prompt 不同**，是设计矛盾。 | extract() |
| 3 | **Medium** | `_build_prompt_v2` 的 Few-Shot Example **类型推断不准确**：对泛型 `list[str]` 显示为空列表，但 PDField(default_factory=list) 也是空列表，fine。但 `sequence`、`Optional` 等复杂类型会出问题。 | _build_prompt_v2 |
| 4 | **Medium** | `smart_html_truncate` 的 `[role=main]` 模式写成了字面量 `<[role=main]...>`，而 HTML 中没有这样的 tag，不会有任何匹配。应该匹配 `<div role="main">` 等。 | smart_html_truncate |
| 5 | **Low** | `_try_llm` 中 `hasattr(settings.llm.api_key, 'get_secret_value')` 检查可以用 `SecretStr` 原生 API 更优雅。 | _try_llm |

---

### 2. cleaner.py — ⭐⭐⭐⭐⭐⭐⭐⭐ (8/10)

**亮点**:
- ✅ `clean_text` 实现完整：HTML entity decode、control char 移除、strip HTML、Unicode NFKC 归一化、whitespace 折叠
- ✅ `trim_html_for_llm` 支持注释移除、非语义标签剥离、属性剥离、Unescape、whitespace 折叠
- ✅ `clean_price` 支持多币种、多货币符号、JPY/CNY 语境消歧
- ✅ `clean_date` 覆盖 ISO 8601、US short、YMD、DMY/MDY heuristic、相对时间等 5 种格式
- ✅ `clean_url` 剥离 tracking params、resolve relative、strip fragment
- ✅ `clean_record` 按字段名模式自动分派清洗器
- ✅ `Cleaner` 类实现了 pipeline 兼容的 `clean(html) -> str` 接口

**问题**:

| # | 严重度 | 问题 | 位置 |
|---|--------|------|------|
| 1 | **Low** | `_DATE_FORMATS` 中 ISO 格式的正则 `([+-]\d{2}:?\d{2}|Z)?` 在 Python re 中 `|` 优先级问题：实际等价于 `([+-]\d{2}:?\d{2})|(Z)?`，但括号括起来了，fine。但 `Z` 出现时不捕获为 group(7) 也没用。 | clean_date |
| 2 | **Low** | `clean_price` 中 Japanese indicator `[ぁ-んァ-ン]` 缺少平假名「ん」之后、Unicode 区块边界的全面涵盖，不过实用场景足够了。 | clean_price |
| 3 | **Info** | `TRIM_HTML_FOR_LLM_ADDENDUM` 作为模块级注释变量很创意，但 `ai_extractor.py` 的 `_semantic_trim` 和 `smart_html_truncate` 并未引用 cleaner 中的 `trim_html_for_llm`，导致两套相似的裁剪代码并存。建议归一化。 | 模块边界 |

---

### 3. sel_healer.py — ⭐⭐⭐⭐ (4/10)

**亮点**:
- ✅ 有 `SemanticRelocator` 类生成冗余选择器（ID-based, Class-based, Tag-based）
- ✅ `heal` 方法使用 httpx 重试请求，复用 ctx 中的 proxy/user-agent
- ✅ 使用 lxml 测试选择器有效性

**问题**:

| # | 严重度 | 问题 | 位置 |
|---|--------|------|------|
| 1 | **Critical** | **`SelHealer.heal()` 实际只是重新请求页面，没有做选择器自愈**。方法签名 `async def heal(self, url: str, ctx) -> str | None` 返回的是 HTML 字符串（重新 HTTP GET），而不是选择器字符串。这使得 `ExtractStage` 中 `ctx.raw_html = healed` 虽然语法正确，但**这只是重试了 HTTP 请求**，并没有试图通过不同的选择器定位目标元素。自愈（self-healing）名不副实。 | heal() |
| 2 | **High** | `SemanticRelocator` 的 `heal()` 方法虽然存在但**从未被 `SelHealer` 调用**。`SelHealer.heal()` 自己实现了 HTTP re-request 而没有调用 `self._relocator.heal()`。 | 类间调用 |
| 3 | **Medium** | `SemanticRelocator.generate_redundant_selectors` 生成的 CSS/XPath 混用（`//*[@id='...']` XPath vs CSS selectors），而 `_test_selector` 虽然正确处理了 `//` 前缀，但 CSS 选择器 `#foo` 和 XPath `//*[@id='foo']` 测试方法不同，混合使用可能导致误判。 | 选择器格式 |
| 4 | **Low** | 依赖 `lxml` 但未在 requirements 或模块导入时给出友好错误提示；`from lxml import html as lhtml` 若缺失则整个模块静默失败。 | _test_selector |

---

### 4. cross_validator.py — ⭐⭐⭐⭐⭐⭐⭐ (7/10)

**亮点**:
- ✅ 实现了完整的 5 源交叉验证：JSON-LD、Microdata、OpenGraph、Meta tags、LLM
- ✅ 带权重的源头加权体系（json_ld:5 > microdata:4 > opengraph:3 > llm:2 > meta:1）
- ✅ Jaccard 相似度聚类对相似值进行分组投票
- ✅ 返回置信度、源一致性计数、所有源结果
- ✅ 被 `ExtractStage._try_http` 正确调用用于字段验证

**问题**:

| # | 严重度 | 问题 | 位置 |
|---|--------|------|------|
| 1 | **Medium** | **`_from_microdata` 的 Regex 过于脆弱**。它假设 Microdata 格式为 `<tag itemprop="field">value</tag>`，但 Microdata 允许值在 `content` 属性中（`<meta itemprop="price" content="19.99">`）或嵌套在不同标签中。这会导致大量遗漏。 | _from_microdata |
| 2 | **Medium** | `_from_jsonld` 中的 pattern 缺少 `<script` 前的起始 tag 开始符 `'<script'`（第 2 行），会被当成内容一部分。实际上 `re.finditer` 的搜索模式 `r'script[^>]*type=...'` 前面的 `'<'` 缺失会导致匹配 `xxxscript type=...>` 这类错误内容。 | _from_jsonld (第 2 行 pattern) |
| 3 | **Medium** | `_from_jsonld` 只查 root 级字段，不查嵌套字段。而通用 schema 如 Product 的 `offers` 中嵌套 price 就无法解析。 | _from_jsonld |
| 4 | **Low** | 聚合结果只用 `best_value` （取第一个值），但 `clusters[0]` 的格式是 `[repr_value, [values], weight]` 而非 `{repr: ..}`。代码逻辑上一开始写的是 `cluster[0]` 作为值，但没有显式命名为 `repr`。实际正确但可读性差。 | validate() |

---

### 5. mobile_api_sniffer.py (位于 routing/, 非 extraction/) — ⭐⭐⭐⭐⭐⭐ (6/10)

**亮点**:
- ✅ 实现了基于 URL pattern 的候选端点多级生成（mobile subdomain → API subdomain → JSON path）
- ✅ 异步探测候选端点（HEAD request），缓存结果
- ✅ 在 `ExtractStage._try_http` 中被正确调用：先 probe 获取 API 端点，再获取内容
- ✅ DNS 缓存加速探测

**问题**:

| # | 严重度 | 问题 | 位置 |
|---|--------|------|------|
| 1 | **High** | **模块文件位置与引用路径不匹配**：`pipeline/stages.py` 中 `from ..routing.mobile_sniffer import MobileAPISniffer as MS` 导入成功的前提是 mobile_sniffer.py 在 `apexcrawler/routing/` 下。确实在，所以可导入，但**任务描述中给出了不存在的路径** `extraction/mobile_api_sniffer.py`。 | 物理位置 |
| 2 | **Medium** | `generate_candidates` 会生成大量重复和低质量端点（比如对已有 `/api/` 路径再添加 `/.json` 后缀）。6 个限制是硬性，但如果是相同域名下重复探测，浪费连接。 | generate_candidates |
| 3 | **Low** | `probe()` 方法中 DNS 解析分支的 `headers = {"Host": host}` 只应用于 IP 替换的情况，但非 IP 替换时没有 headers，这可能导致某些 server 返回不同内容。 | probe() |

---

### 6. extraction/__init__.py — ⭐ (1/10)

**问题**:

| # | 严重度 | 问题 |
|---|--------|------|
| 1 | **High** | **文件为空**。没有导出任何公共 API，使用者必须各自 import 具体模块。虽然 Python 允许，但不符合包的规范做法。 |
| 2 | **Medium** | 应至少导出一组常用的类（`AIExtractor`, `Cleaner`, `SelHealer`, `CrossValidator`）方便调用。 |

---

### 7. pipeline/stages.py (ExtractStage) — ⭐⭐⭐⭐⭐⭐⭐ (7/10)

**亮点**:
- ✅ 完整的三级降级逻辑：HTTP (含 mobile API probe) → Browser → SelHealer
- ✅ 正确调用了 `CrossValidator` 在 HTTP 获取后做字段验证
- ✅ 正确调用了 `MobileAPISniffer.probe()` 优先使用 API 端点
- ✅ 正确调用了 `Cleaner.clean()` 清洗 HTML
- ✅ 正确调用了 `SelHealer.heal()` 降级 recovery
- ✅ Brotli decompression 支持
- ✅ DNS cache resolve 优化
- ✅ 异常处理完善，每步都有 try/except

**问题**:

| # | 严重度 | 问题 |
|---|--------|------|
| 1 | **Critical** | **`AIExtractor` 完全未被 ExtractStage 使用**。ExtractStage 只做了 HTML 获取和清洗，完全没有调用 `ai_extractor.py` 中的任何方法进行结构化数据提取。提取逻辑在 pipline 中是缺失的——PipelineContext 的 `extracted_data` 字段在整个 ExtractStage 中从未被赋值。 |

---

### 8. llm/ 目录 — ⭐ (0/10)

**问题**:

| # | 严重度 | 问题 |
|---|--------|------|
| 1 | **Critical** | **`llm/` 目录完全不存在**。代码未创建该目录，没有任何 LLM 客户端封装、基类、接口或工具。AIExtractor 中的 LLM 调用是直接在 `_try_llm` 方法内用 httpx 调用 OpenAI-compatible API 的，没有通过任何抽象的 LLM 模块。 |
| 2 | **Medium** | 没有统一的 LLM client 抽象（如 `BaseLLM`、`ChatCompletion` 接口），导致 AIExtractor 硬编码了 httpx 调用，且 prompt 构建、token 计数、retry/backoff 等通用功能无法复用。 |

---

## 关键发现（按严重度排序）

### 🔴 Critical (必须修复)

1. **AIExtractor 未集成到 Pipeline** — `ExtractStage` 在整个 pipeline 中从未创建或调用 `AIExtractor`，提取出来的结构化数据（title, price, description 等）从未通过 schema 路径验证或存储。`PipelineContext.extracted_data` 保持为 `None`。

2. **llm/ 目录完全缺失** — 没有任何 LLM 抽象层。`AIExtractor._try_llm` 中的 LLM 调用硬编码了 httpx + OpenAI compat 协议，没有 retry、token 管理、模型路由、替换回溯等能力。

3. **SelHealer 名不副实** — `heal()` 方法只是重新 HTTP GET 页面，没有做真正的选择器自愈。`SemanticRelocator.heal()` 存在但从未被调用。

### 🟠 High (必须修复)

4. **extract_structured 与 _extract_structured 功能重复** — 两个方法都解析 JSON-LD / OG / Meta，一个返回 dict，一个返回 Model。`_try_llm` 调用 `extract_structured`，`extract` 调用 `_extract_structured`。维护成本翻倍。

5. **降级链 Step 2 & Step 3 都是 LLM 调用，设计矛盾** — Step 2 的 `_try_llm` 和 Step 3 的 `_build_prompt_v2` + `self._llm.generate` 都是 LLM 路径但 prompt 不同。要么二选一，要么 Step 3 为 Step 2 的 retry。

6. **CrossValidator._from_jsonld 缺少 `<` 前缀** — pattern 以 `r'script[^>]*...'` 开头而非 `r'<script[^>]*...'`，可能导致误匹配。

7. **CrossValidator._from_microdata 正则过于脆弱** — 只能捕获 `<tag itemprop="x">value</tag>`，错过 `content` 属性和嵌套结构。

### 🟡 Medium (建议修复)

8. **[role=main] 选择器在 smart_html_truncate 中不会匹配任何元素** — 写成了 `<[role=main]...>` 字面量，需改为 `<div[^>]*role="main"[^>]*>...`。

9. **Cleaner.trim_html_for_llm 与 AIExtractor 中的裁剪逻辑重复** — 两套 HTML 缩减代码相似但不一致。应合并到 cleaner 中以公共函数形式提供。

10. **extraction/__init__.py 为空** — 应导出公共 API。

### 🔵 Low (值得注意)

11. `clean_date` 中 ISO 8601 时区 Z 的处理不消费 group
12. `_build_prompt_v2` 的 Few-Shot Example 对复杂类型推断可能不准确
13. `_try_llm` 中 `SecretStr.get_secret_value()` 处理方式不优雅

---

## 总结

### 整体评价

| 维度 | 评价 |
|------|------|
| **架构设计** | ⭐⭐⭐⭐⭐⭐⭐⭐ (8/10) — 降级链、多源验证、自愈设计思路很好，逻辑分层清晰 |
| **实际实现** | ⭐⭐⭐⭐ (4/10) — AIExtractor 虽已实现 `_try_llm` 但仍未接入管线，关键集成缺失 |
| **代码正确性** | ⭐⭐⭐⭐⭐ (5/10) — 有多处正则缺陷和逻辑断点 |
| **模块完整性** | ⭐⭐⭐ (3/10) — llm/ 目录不存在，`__init__.py` 为空，独立完整性差 |
| **测试友好度** | ⭐⭐⭐ (3/10) — 依赖 lxml、httpx、pydantic 但无 mock/interface 隔离 |

### 关键行动项

1. **【P0】将 AIExtractor 接入 ExtractStage** — 在 ExtractStage 中实例化 AIExtractor（或通过 DI 注入），在获取 HTML 后调用 `extract(html, schema)` 填充 `ctx.extracted_data`。
2. **【P0】创建 llm/ 模块** — 定义 `BaseLLM` 协议/接口，实现 `OpenAILLM`、`DeepSeekLLM`、`OllamaLLM` 等客户端，包含 retry、token 计数、prompt 模板等功能。
3. **【P1】修复 CrossValidator 的正则缺陷** — `_from_jsonld` 补上 `<` 前缀；`_from_microdata` 支持 `content` 属性。
4. **【P1】合并 extract_structured 与 _extract_structured** — 统一为一个方法，消除代码重复。
5. **【P1】修复 SelHealer 自愈逻辑** — 让 `SelHealer.heal()` 调用 `SemanticRelocator.heal()` 实现真正的选择器自愈。
6. **【P2】归一化 HTML 裁剪逻辑** — Cleaner 提供 `trim_html_for_llm`，AIExtractor 调用它而非自实现。
7. **【P2】修复降级链 Step 2/3 矛盾** — Step 3 改为 Step 2 的 retry，或统一为一个 LLM 路径。
8. **【P2】填充 extraction/__init__.py 的公共导出**。
