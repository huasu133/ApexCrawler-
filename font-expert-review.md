# 字体反爬专家审查报告

**审查范围**: `/apexcrawler/anti_font/` (6 files) + `/apexcrawler/utils/brotli_support.py` + `/apexcrawler/cache/`
**审查日期**: 2026-06-06
**审查深度**: 实地逐行代码审查

---

## 1. 已实现 ✅

### 1.1 `font_cracker.py` — FontTools 基础破解
- **`_find_font_urls()`**: 支持从 HTML 中提取 `@font-face` 标准字体 URL 和 base64 inline 字体（含 `\.woff2?` 正则匹配 `.woff` 和 `.woff2`）
- **`_decode_with_fonttools()`**: 使用 `fontTools.ttLib.TTFont` 解析字体文件（TTF/WOFF），提取 cmap glyph 到字符的映射
- 缓存机制: 支持 `cache_backend` 注入，使用 SHA256 URL 哈希作为缓存 key（字面是 URL 哈希而非"内容哈希"，见下方分析）
- URL 级别的缓存存储（pickle 序列化）

### 1.2 `ocr_engine.py` — 完整 OCR 引擎
- **三后端支持**: `ddddocr`、`paddleocr`、`tesseract` 三选一
- **置信度评分系统**: 三重评分引擎:
  - `engine_confidence`: 各引擎原生置信度（PaddleOCR 使用 line-level confidence 取平均；ddddocr 使用字符合法性比率）
  - `coherence_score`: 英文字典词匹配 / 中文高频字匹配
  - `entropy_score`: 字符分布熵归一化
  - 最终合成 `confidence = 0.4 * engine + 0.4 * coherence + 0.2 * (1 - entropy)`
- **双引擎投票**: `recognize_with_voting()` 方法，同时调用 `ddddocr` 和 `paddleocr`，文本一致则返回，否则取置信度高的
- **批量识别**: `recognize_batch()` 支持多图识别
- **Glyph-to-Char 映射**: `build_glyph_map()` 支持 OCR 逐字识别字形并建立映射，默认置信度阈值 0.8
- 完整的文档字符串和类型注解

### 1.3 `dom_fixer.py` — DOM 修复
- **数值字符实体解码**: `_decode_numeric_entities()` 支持 `&#xHEX;` 和 `&#DEC;`
- **隐藏样式修复**: `_fix_inline_styles()` 修复 `display:none` → 移除、`visibility:hidden` → visible、`opacity:0` → 1、`font-size:0` → 16px、`color:transparent` → initial、`text-indent: -9999px` → 0
- **CSS 伪元素内容注入**: `_inject_pseudo_content()` 从 `<style>` 中提取 `::before`/`::after` 的 `content` 属性，将其注入到匹配的元素后面（支持 class 选择器 `.xxx`、ID 选择器 `#xxx`、标签名选择器）
- **Shadow DOM 检测**: `pierce_shadow_dom()` 通过 JS 中的 `attachShadow()` 正则提取 shadow content
- **CSS 偏移检测**: `detect_offsets()` 检测 `left/right/top/bottom/margin-left/margin-top` 中绝对值 > 5000 的极端偏移
- **隐藏文本提取**: `extract_hidden_text()` 通过 CSS 隐藏类名（hidden、sr-only 等）提取 DOM 中隐藏的文本

### 1.4 `wasm_interceptor.py` — WASM CDP Hook
- **4 个 WASM API 拦截**: `WebAssembly.instantiate`、`instantiateStreaming`、`compile`、`compileStreaming`
- **SIMD 代码区检测**: `hasSIMD()` 仅扫描 WASM 的 code section (id=10)，避免数据段中的 `0xFD` 误报
- **SIMD 降级**: 检测到 SIMD 时 console.warn 但不阻止执行（downgrade 而非 block）
- **暴露统计接口**: `window.__apex_wasm.{totalModules, simdNeutralized, stats, warnings}`
- **WebGPU 拦截**: 拦截 `navigator.gpu.requestAdapter` 强制 low-power 回退
- **Python 集成**: `WASMInterceptor` 类提供 `get_init_script()`, `inject()`, `get_stats()` 方法

### 1.5 `wasm_parser.py` — WASM 解析
- **WASM 模块 URL 检测**: `detect_wasm_modules()` 从 HTML 提取 WASM 引用
- **包头解析**: `parse_header()` 验证 `\x00asm` magic 和版本号
- **Section 解析**: `_parse_sections()` 完整解析 13 种 section (id 0-12)，含自定义 section 名称
- **SIMD 检测**: `detect_simd()` 扫描 `0xFD` 前缀
- **字符串提取**: `extract_strings()` 提取可读 ASCII 字符串
- **反爬签名检测**: `detect_anti_crawl()` 检测 15 种已知反爬系统签名（Cloudflare、Akamai、DataDome、PerimeterX、Imperva 等）

### 1.6 `font_recovery.py` — 多级回退链
- **四级策略**: `STRATEGIES = ["fonttools", "ddddocr", "paddleocr", "contour_match"]`
- **回退机制**: 按 `fonttools → paddleocr → ddddocr` 顺序尝试

### 1.7 Brotli 支持 ✅
- `utils/brotli_support.py` 实现了 `supports_brotli()` 和 `decompress_brotli()` 以及 `BROTLI_ACCEPT_ENCODING` 常量
- 但 **`font_cracker.py` 中的 `_decode_with_fonttools()` 和 `_decode_with_ocr()` 未显式处理 Brotli/WOFF2 字体内容**。下载 HTTP 响应时未对 `Content-Encoding: br` 做解压处理。

### 1.8 Redis 缓存后端 ✅
- `cache/backends/redis.py` 完整实现了 Redis 连接池、get/set/delete/exists/incr/clear_namespace

### 1.9 OCR 置信度评分 ✅
- `ocr_engine.py` 中的 `OCRResult` 包含完整的 4 维置信度评分体系（engine_confidence、coherence_score、entropy_score、最终合成 confidence）

---

## 2. 未实现/待修复 ❌

### 2.1 WASM 加密字体 CDP Hook（P0#8）❌
- **文件**: `wasm_interceptor.py`
- **问题**: WASM 拦截脚本仅 LOG/WARN 检测到 SIMD 的模块，并未真正阻止——注释明确写着 "downgrade"。评审要求的是 **WASM 加密字体的 CDP Hook**，即拦截 WASM 对字体的加密解密操作。当前实现只检测 SIMD，无法拦截/解密 WASM 加密的字体数据。
- **状态**: 仅实现了 SIMD 检测+警告，未实现 WAF（WebAssembly 加密字体）的解密 Hook

### 2.2 CSS `getBoundingClientRect()` 通用还原（P1）❌
- **文件**: 整个 `anti_font/` 目录
- **问题**: 完全未实现。许多反爬系统使用 `getBoundingClientRect()` 的文字坐标还原（如把字符偏移到实际位置的结合文本通过 JS 运行时 canvas/Rect 测量还原）。`dom_fixer.py` 的 `detect_offsets()` 仅检测 inline style 中的极端负数偏移，无法处理 JS 运行时通过 `getBoundingClientRect()` 动态计算的偏移。

### 2.3 伪元素 `attr()` / `counter()` 支持（P1）❌
- **文件**: `dom_fixer.py`
- **问题**: `_inject_pseudo_content()` 仅提取 `content: "text"`（字符串文本）内容。CSS 伪元素的 `content` 属性支持 `attr(data-xxx)` 和 `counter(xxx)` 两种功能，当前均未实现。
  - `attr()` 需要读取目标元素的指定 attribute 值
  - `counter()` 需要维护 CSS 计数器状态

### 2.4 `base64` + `unicode-range` + Variable 字体支持（P1）❌
- **文件**: `font_cracker.py`
- **问题**:
  - base64 font: 当前 `_find_font_urls()` 的正则 `url\(data:font/[^;]+;base64,([^"')]+)` 仅提取 base64 字符串，但 `_decode_font()` 调用 `httpx.get(url)` 尝试下载 base64 string（会失败——base64 字符串不是 HTTP URL）。base64 字体解码从未真正实现。
  - `unicode-range`: 完全未实现。许多反爬使用 `@font-face` 配合 `unicode-range` 分段，需要提取 `unicode-range` 声明才能将 glyph 映射到正确的 Unicode codepoint。当前 `_find_font_urls()` 仅提取 URL，不提取 `unicode-range`。
  - Variable 字体: 完全未实现。Variable fonts（如 OpenType 1.8+）使用 `fvar` table，cmap 映射方式不同，当前 FontTools 的 `getBestCmap()` 可能无法正确处理。

### 2.5 Shadow DOM `closed` 模式支持（P1）❌
- **文件**: `dom_fixer.py`
- **问题**: `pierce_shadow_dom()` 仅通过正则 `attachShadow\(\{[^}]*\}\)` 提取 JS 中的 shadow root 内容。对于 `mode: 'closed'` 的 Shadow DOM：
  1. 无法通过 JS 的 `element.shadowRoot` 访问
  2. 无法通过正则提取内容（closed 模式通常动态插入 shadow tree）
  3. 当前实现完全无法处理 closed 模式

### 2.6 `font_recovery.py` — `contour_match` 策略缺失 ❌
- **文件**: `font_recovery.py`, line 8
- **问题**: `STRATEGIES = ["fonttools", "ddddocr", "paddleocr", "contour_match"]` 声明了 4 种策略，但 `_try_strategy()` 仅实现了前 3 种，`contour_match` 没有任何实现——调用时返回空 `{}`。且 `_fallback_order` 只有 3 种策略，忽略了 `contour_match`。

### 2.7 缓存是 URL 哈希 → 非"内容哈希" ❌
- **文件**: `font_cracker.py`, line 38
- **问题**: 缓存 key 使用 `hashlib.sha256(url.encode()).hexdigest()[:16]`（URL 哈希），而非评审要求的**内容哈希**。这意味着：
  - 同一 URL 返回不同字体内容（动态字体）时，缓存会返回旧数据
  - 不同 URL 但内容相同时，缓存会重复存储
  - 内容哈希应基于字体文件内容的 SHA256

### 2.8 `font_cracker.py` — `_decode_with_ocr()` `except ImportError` 静默吞异常 ❌
- **文件**: `font_cracker.py`, line 109
- **问题**: 最终的 `except ImportError` 和 `except Exception as e` 都仅 `logger.warning` 并返回 `{}`，OCR 失败时外部无法区分"字体无加密"和"OCR 工具未安装"

### 2.9 WOFF2/Brotli 在字体下载链中未集成 ❌
- **文件**: `font_cracker.py`, lines 64-75
- **问题**: 虽然系统有 `utils/brotli_support.py`，但 `_decode_with_fonttools()` 和 `_decode_with_ocr()` 中的 `httpx.get()` 未设置 `Accept-Encoding: br`，也未对 woff2 响应做 brotli 解压。`fontTools.ttLib.TTFont` 本身支持 WOFF2，前提是 `fonttools[woff]` 已安装且有 brotli 库，但 httpx 超时/解码层无对应处理。

---

## 3. 代码质量问题 ⚠️

### 3.1 重复的 OCR fallback 实现
- `font_cracker.py` 的 `_decode_with_ocr()` (line 80-113) 和 `font_recovery.py` 的 `_decode_with_ddddocr()`/`_decode_with_paddleocr()` 都实例化 `FontCracker` 并调用 `_decode_with_ocr()` —— 两者功能完全重复但实现路径不同
- `font_recovery.py` 的 `_try_strategy()` 对 "ddddocr" 和 "paddleocr" 统一调用 `fc._decode_with_ocr()`，内部实际也只会使用 FontCracker 内硬编码的 PIL + OCREngine 流程，无法选择后端引擎

### 3.2 `dom_fixer._inject_pseudo_content()` 正则处理有缺陷
- 对同一选择器在 HTML 中出现多次时，会注入多个重复的 `<span data-apex-pseudo-content>`
- class 选择器匹配使用 `re.DOTALL | re.IGNORECASE`，但部分场景下 class 值可能出现在其他属性值中导致误匹配
- 非嵌套标签处理可能导致 HTML 结构被破坏

### 3.3 `font_cracker._decode_with_fonttools()` 的 cmap 使用可能错误
- 第 73 行 `mapping[glyph_name] = char` 建立的是 `glyph_name → char` 映射
- 但 FontTools 的 `cmap` 是 `codepoint → glyph_name`，不转换为 `character → glyph_name` 可能更符合使用场景

### 3.4 `ocr_engine.recognize_with_voting()` 创建重复 PaddleOCR 实例
- 每次投票调用都创建新的 `PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)`，这是重量级操作（加载模型）
- `_run_ocr()` 对 `paddleocr` 后端会无条件创建新实例而非使用 `self._engine`

### 3.5 缺失测试
- 整个 `anti_font/` 目录无任何测试文件

### 3.6 `wasm_interceptor.py` 中的 GPU 指纹库和 Docker 指南
- `GPU_FINGERPRINT_LIBRARY` 字典和 `get_gpu_profile()` 函数被非直接放在 `WASMInterceptor` 类所在文件尾部，职责边界模糊，与其他已有指纹模块 (`fingerprint/`) 不一致

### 3.7 `font_recovery.py` `_try_strategy()` 日志级别
- 第 32 行 `result` 在非 `fonttools` 策略下始终返回 `None`（因为 `_decode_with_ddddocr()` 和 `_decode_with_paddleocr()` 都调用 `fc._decode_with_ocr()` 返回 dict，但 `_try_strategy` 对这两种策略未定义返回值处理——实际返回结果但 `if result` 判断可能为 `{}`）

---

## 4. 与 6 专家评审对比

### 已修复 ✅

| 评审 # | 项目 | 状态 | 说明 |
|--------|------|------|------|
| P0#7 | WOFF2/Brotli 支持 | 🟡 **部分实现** | `utils/brotli_support.py` 实现了 brotli 解压函数，`font_cracker.py` 正则匹配 `.woff2?` 但字体 HTTP 下载层未集成 brotli 解码 |
| P0#9 | Redis 内容哈希缓存 | 🟡 **部分实现** | Redis 后端(`cache/backends/redis.py`)完整可用；但 `font_cracker.py` 缓存 key 使用 **URL 哈希**而非**内容哈希**，且未和 `cache/Cache` 类集成（直接操作 pickle） |
| P1 | OCR 置信度评分+双引擎 | ✅ **已实现** | `ocr_engine.py` 完整的 4 维置信度评分 + 双引擎投票 |
| P1 | 伪元素 content 注入 | 🟡 **部分实现** | `::before`/`::after` 的基础 `content: "text"` 已实现；`attr()` 和 `counter()` 未实现 |

### 仍未修复 ❌

| 评审 # | 项目 | 状态 | 说明 |
|--------|------|------|------|
| P0#8 | WASM 加密字体 CDP Hook | ❌ **未真正实现** | 当前 WASM 拦截仅对 SIMD 模块做 LOG/WARN，无任何 WAF 加密字体解密能力 |
| P1 | CSS getBoundingClientRect 通用还原 | ❌ **完全未实现** | 仅实现了 inline style 的极端偏移检测，无运行时 JS 坐标还原 |
| P1 | 伪元素 attr()+counter() | ❌ **未实现** | `dom_fixer._inject_pseudo_content()` 仅支持字符串字面量 |
| P1 | base64+unicode-range+variable 字体 | ❌ **未实现** | base64 font 无法正常运行；unicode-range 未提取；variable font 未处理 |
| P1 | Shadow DOM closed 模式 | ❌ **未实现** | `pierce_shadow_dom()` 仅能处理 open 模式的部分场景 |
| P1 | Routing/Registry 整改 | ❌ **本项目未涉及** | 属于 python-architect 领域的全局整改 |

---

## 总结

**总体评分: 4.5 / 10**（较评审前的 2.0 有提升，但离目标 7.0 还有显著差距）

**亮点**: OCR 引擎是三模块中最完整的实现，置信度评分体系设计合理；DOM 修复覆盖了大部分 CSS 隐藏技巧；WASM 分析器的 section 解析完整。

**核心短板**:
1. **WASM 加密字体无应对** — 认证报告中的 P0#8 核心痛点
2. **getBoundingClientRect + Shadow DOM closed + unicode-range + variable 四个 P1 关键功能完全空白**
3. **base64 font 实际不可用**（`font_cracker.py` 线路 53-54 的下载必然失败）
4. **缓存用 URL 哈希而非内容哈希**，与评审要求的设计不一致
5. **font_recovery.py 的 contour_match 策略缺失**，宣布 4 种策略但只实现了 3 种
