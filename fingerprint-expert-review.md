# 指纹与反检测模块审查报告

## 总体评分: 6.5/10

项目具备良好的反检测架构设计思路，指纹维度覆盖广泛，但存在多处 **代码异味、未实现存根、逻辑缺陷和注释不一致**，表明部分模块处于"架构已完成但实现未同步"的状态。最关键的问题是 **实际代码路径与审查清单文件名的严重不匹配**（多个审查项在代码库中根本不存在同名文件），以及 **指纹注入引擎（consistency.py）没有被任何引擎实际调用**。

---

## 各文件审查

### 1. `fingerprint/consistency.py` — 评分: 7/10

**核心问题：**

1. **P0 — CDP 注入脚本未被任何引擎调用**。CamoufoxEngine、CloakedEngine、PatchedEngine 都没有调用 `DeviceProfile.cdp_inject_script()`。PatchedEngine 有独立的 `_STEALTH_JS` 常量，CloakedEngine 依赖 CloakBrowser 二进制层。`consistency.py` 提供的 6 层注入实际处于"孤岛"状态。
2. **P1 — `navigator.vendor` 仅注入到 JS 层，Canvas/WebGL getParameter 在 Chrome headless 中有已知兼容问题**。`configurable: false` 对 `navigator.userAgent` 等属性在 headless 模式下可能抛异常（Chromium 限制）。
3. **P2 — PCG 随机种子精度问题**：BigInt `6364136223846793005n` 在 JavaScript BigInt 运算中性能开销大；`state >> 33n` 右移后 `Number()` 转换对 64 位 BigInt 丢失精度。
4. **P2 — `AudioContext` / `webkitAudioContext` 的 `||` 短路在 Safari 中 OK，但 `const AC = AudioContext || webkitAudioContext` 在 Playwright Chromium 中 `webkitAudioContext` 未定义会怎样？实际不会报错，但代码风格上不够严谨。**
5. **P3 — `validate()` 方法接受度有限**：Mac Firefox profile 的 validator 会触发 `"macOS platform but non-Apple GPU"` 因为 FireFox 的 WebGL renderer 仍然写的是 NVIDIA ANGLE。
6. **P3 — 缺少 `navigator.languages` 注入**（`navigator.language` 有注入但 `navigator.languages` 无，这是 fingerprintjs 等检测库的关键信号）。

**建议：**
- 将 `cdp_inject_script()` 集成到 PatchedEngine（`add_init_script`）或 BaseEngine 基类中
- 补充 `navigator.languages` 和 `navigator.mimeTypes` 注入
- 对 Mac Firefox profile 做 GPU vendor 兼容性调整
- 考虑代码分离将 `DEVICE_PROFILES` 列表和数据类放到独立文件

### 2. `fingerprint/injector.py` — 评分: 0/10

**文件不存在。** 审查清单指定的路径 `/apexcrawler/fingerprint/injector.py` 在现有代码库中没有对应文件。建议检查是否需要创建，或者其功能已合并到 `consistency.py` 的 `cdp_inject_script()` 方法中。

### 3. `fingerprint/profiles.py` — 评分: 0/10

**文件不存在。** 同样，审查清单中的 `fingerprint/profiles.py` 在代码库中不存在。指纹配置文件数据当前位于 `consistency.py` 中（`DEVICE_PROFILES` 列表）和 `http/tls_router.py`（`PROFILES`）。

### 4. `fingerprint/gpu_profiles.py` — 评分: 0/10

**文件不存在。** 没有独立的 GPU 配置文件。GPU 信息（`webgl_renderer` / `webgl_vendor`）嵌入在 `consistency.py:DeviceProfile` 的固定字符串中。

### 5. `fingerprint/webrtc.py` — 评分: 0/10

**文件不存在。** WebRTC 泄露防御仅在 CamoufoxEngine 中通过 Firefox preference `media.peerconnection.enabled = False` 处理。Chromium 引擎（CloakedEngine、PatchedEngine）**没有任何 WebRTC 防护**。

### 6. `fingerprint/__init__.py` — 评分: 5/10

**文件存在但内容极少。** 阅读时显示为图片附件，没有实质性导出。建议：
- 至少导出 `DeviceProfile`、`get_profile`、`DEVICE_PROFILES`
- 如果 `injector.py` 等功能确认合并，更新 `__all__` 和文档字符串

### 7. `evasion/device_profile.py` — 评分: 0/10

**文件不存在。** 审查清单路径 `/apexcrawler/evasion/device_profile.py` 不存在。`device_profile` 概念存在于 `fingerprint/consistency.py`（`DeviceProfile` 数据类）和 `http/headers.py`（HTTP 层），但没有 `evasion/device_profile.py`。

### 8. `evasion/passive_signals.py` — 评分: N/A

**文件不存在**于 `evasion/` 下。但其等价功能存在于 `behavior/passive_signals.py`。以该文件为准评分: **8/10**

**问题：**
1. **P2 — `inject_dns_prefetch()` 中的 `_warm()` 异步任务错误处理有问题**：`task.add_done_callback(lambda t: logger.error(f"warm_up failed: {t.exception()}") if t.exception() else None)` — 这是合理的异步日志模式，但 `_warm()` 中的 `page.evaluate` 使用了一个多余的引号 `""""` 开头的字符串，导致语法错误（多了一个引号）。
2. **P2 — `simulate_tab_switch()` 中 `document.hidden` 的 `defineProperty` 不会触发 Page Visibility API 的真实浏览器行为**。Playwright 页面虽然会收到事件，但 `document.hidden` 的 `defineProperty` 只是伪造了属性值，没有实际改变页面可见性。Firefox headless 可能正确触发，Chromium 则不。更好的做法：通过 Playwright 的 CDP `Page.setLifecycleEventsEnabled` 或 `Emulation.setVisible`。
3. **P3 — `monitor_sendbeacon` 拦截的是已挂起（bound）的原生函数**，如果页面脚本在注入前已经缓存了 `navigator.sendBeacon` 引用，则拦截无效。
4. **P3 — `SessionBehavior` 的 wpm 稳定性不错，但缺乏时区上下文对齐**。如果 `profile.timezone` 是 America/New_York 但本地时间是 UTC+8，用户活跃时间不匹配。

**建议：**
- 修正 `inject_dns_prefetch` 中的字符串引号语法错误（`""""(targets)` → `"""`）
- 为 Chromium 引擎添加 CDP-based 可见性伪造
- 考虑为 `SessionBehavior` 添加时区对齐的活跃时段调节

### 9. `evasion/subresource_loader.py` — 评分: N/A

**文件不存在**于 `evasion/` 下。等价功能存在于 `engines/subresource.py`。评分: **7/10**

**问题：**
1. **P1 — `ensure_subresource_load` 对 `SKIP_PATTERNS` 的匹配逻辑过于简单**。使用 `fnmatch.fnmatch(host, pattern)` 时，模式 `"*fingerprintjs*"` 只匹配完整的 host（如 `fingerprintjs.com`），但不匹配子域名 `cdn.fingerprintjs.com`。`fnmatch` 不支持路径 glob 的所有特性。
2. **P1 — 缺少对子资源加载完成后的 clean-up**。`page.route("**/*", _router)` 在函数执行后没有被 `page.unroute(...)` 移除，后续页面可能继续加载阻塞指纹资源的路由模式。
3. **P2 — 长时间的 `asyncio.sleep(extra)` 阻塞事件循环**。`extra` 可能为 6-9 秒，在这段时间内没有处理和卸载路由。应该使用 `await asyncio.sleep` 在引擎的 navigate 流程中自然等待。
4. **P2 — `wait_for_load_state("domcontentloaded")` 后直接 sleep 0.5s**，应当等待 `networkidle` 以确保子资源已发起请求。

**建议：**
- 使用 `re.match` 替代 `fnmatch`，或确保通配符覆盖子域名
- 添加 `page.unroute("**/*")` 在函数返回前
- 将 `extra` 等待移到引擎层统一管理
- 修复子域名匹配问题：`re.search(pattern_to_regex(pattern), host)`

### 10. `evasion/connection_reuse.py` — 评分: N/A

**文件不存在。** 等价功能存在于 `http/connection_pool.py`（`StealthProxy` + `ConnectionReuseManager`）。评分: **7/10**

**问题：**
1. **P1 — `StealthProxy.start()` 中的端口捕获逻辑脆弱**：`self._site._server.sockets[0].getsockname()[1]` 直接访问下划线私有属性（`_server`, `sockets`），当 `port=0` 时依赖 aiohttp 内部实现细节。在 aiohttp 不同版本中可能变更。
2. **P2 — `_handle()` 中 `request.can_read_body` 是属性还是协程？**检查 aiohttp 文档：`can_read_body` 是一个属性（`bool`），使用正确，但 `request.read()` 调用可能触发大量内存分配（大文件）。
3. **P2 — `ConnectionReuseManager` 递增端口号从来不复用已释放端口**。长时间运行后端口号会持续增长，可能与其他服务冲突。
4. **P3 — `StealthProxy` 没有 `__repr__` 或序列化支持**，调试时不够友好。

**建议：**
- 使用 `self._site._server.sockets[0].getsockname()[1]` 改为通过 aiohttp 的 API 或设置固定端口并提前绑定；考虑用 `sockname` 属性的公共 API 替代
- 实现端口回收机制或在 `close_all()` 后重用端口号
- 添加请求体大小限制（如 10MB）以防止内存溢出

### 11. `evasion/dns_cache.py` — 评分: N/A

**文件不存在。** 等价功能存在于 `utils/dns_cache.py`。评分: **7/10**

**问题：**
1. **P2 — `DNSCache.resolve()` 使用 `socket.getaddrinfo(host, 443)` 硬编码端口 443**，对于 HTTP（非 HTTPS）页面会返回错误的端口解析（虽然通常不影响 IP，但如果资源通过不同端口加载则有问题）。更通用的做法应该是 `port=None`。
2. **P2 — 缺少异步 DNS 解析支持**。`socket.getaddrinfo` 是阻塞调用，在异步 Playwright 事件循环中会阻塞整个事件循环约 50-200ms。
3. **P3 — 缓存过期后只清空条目，但没有重试或备选方案**。如果 DNS 临时失败，之前缓存的 IP 可能仍然可用（DNS 缓存策略：TTL 过期但继续服务旧记录）。

**建议：**
- 使用 `asyncio.get_event_loop().getaddrinfo(host, None)` 非阻塞解析
- 支持泛型端口参数而不是硬编码 443
- 实现 Stale-While-Revalidate 策略（TTL 过期后先返回旧记录再异步刷新）

### 12. `evasion/timing_scheduler.py` — 评分: N/A

**文件不存在。** 等价功能存在于 `behavior/timing.py`。评分: **8/10**

**问题：**
1. **P2 — `is_safe_time()` 是一个异步 crawler 最关键的时间维检测防御之一，但当前实现使用固定的 00:00-05:59 窗口**。没有考虑时区！如果 `DeviceProfile.timezone = "America/New_York"`（UTC-5）但主机时间是 UTC+8，两个时区相差 13 小时，实际的"深夜"需要根据 profile timezone 计算，而不是主机时间。
2. **P2 — `content_dwell_time()` 中 `reading_time = text_length / 5.0` 的中文阅读速度模型不适用**。如果目标是中文网站（典型的 cnblogs、zhihu 等），中文阅读速度约为 ~300 chars/min（5 chars/s），实际上这个公式对中英文都合理，但 `sigma` 分布没有按语言区分。
3. **P3 — `CrawlWindow.is_active()` 中的 `*self.work_morning, *self.work_afternoon, *self.evening` 列表展开每次调用都创建新元组**，不是性能问题但可优化为预编译。

**建议：**
- `is_safe_time()` 增加 `timezone` 参数，支持按 profile timezone 计算
- 添加中文文本的阅读速度参数（`chars_per_second`）
- `CrawlWindow.active_windows_today()` 返回类型改为 `list[tuple[float, float]]`

---

## 关键发现（按严重度排序）

### P0 — 致命

| # | 问题 | 涉及文件 | 影响 |
|---|------|---------|------|
| 1 | **`cdp_inject_script()` 6层指纹注入未被任何引擎调用** | `consistency.py`, `patched.py`, `cloaked.py`, `camoufox.py` | 整个 6 层验证体系形同虚设。项目投入了大量精力构建指纹注入代码，但实际运行时未被执行 |
| 2 | **审查清单中 12 个文件，只有 2 个 `fingerprint/` 下文件存在** | 清单所有文件 | 项目代码结构与审查清单严重脱节。`fingerprint/injector.py`、`profiles.py`、`gpu_profiles.py`、`webrtc.py` 都不存在；`evasion/` 目录不存在，其功能分散在 `behavior/`、`http/`、`engines/`、`utils/` 中 |
| 3 | **Chromium 引擎（Cloaked/Patched）完全缺少 WebRTC 防护** | `cloaked.py`, `patched.py` | 即使有 CDP 注入，WebRTC 仍可能泄漏真实 IP，被 WebRTC leak checker 标记 |

### P1 — 严重

| # | 问题 | 涉及文件 | 影响 |
|---|------|---------|------|
| 4 | **`patched.py` 的独立 `_STEALTH_JS` 和 `consistency.py` 的 `cdp_inject_script()` 功能大量重复** | `patched.py`, `consistency.py` | 两个独立的 JS 注入代码，都不完整，且互不知晓对方存在。维护时可能只改一个导致不一致 |
| 5 | **`inject_dns_prefetch()` 有语法错误（多余引号）** | `behavior/passive_signals.py` | JavaScript 代码不会执行；DNS 预取的异步 warm-up 任务会静默失败 |
| 6 | **`ensure_subresource_load()` 路由处理后没有 `unroute`** | `engines/subresource.py` | 指纹脚本（fingerprintjs 等）的路由阻塞会保持到页面关闭。如果有多个页面，策略错误 |
| 7 | **`subresource.py` 的 `SKIP_PATTERNS` 使用 `fnmatch` 匹配 host，不匹配子域名** | `engines/subresource.py` | `cdn.fingerprintjs.com` 可能通过过滤，仍然加载了指纹脚本 |

### P2 — 中等

| # | 问题 | 涉及文件 | 影响 |
|---|------|---------|------|
| 8 | **`navigator.languages` 属性未被注入** | `consistency.py` | fingerprintjs 通过检查 `languages` 和 `language` 的一致性检测 bot |
| 9 | **`passive_signals.py` 的 `simulate_tab_switch()` 不会改变真实页面可见性** | `behavior/passive_signals.py` | 部分高级检测不仅监听事件，还检查 CDP 的 Page Visibility 状态。Chromium headless 也支持 CDP 可见性设置 |
| 10 | **`timing.py` 的 `is_safe_time()` 使用主机时间而不是 profile timezone** | `behavior/timing.py` | 如果 profile 设为美洲时区但主机是亚洲时区，实际活跃时段计算错误 |
| 11 | **`utils/dns_cache.py` 使用同步 `socket.getaddrinfo` 阻塞事件循环** | `utils/dns_cache.py` | 50-200ms 的阻塞可能影响吞吐量 |
| 12 | **TLS 配置存在两套配置文件** | `consistency.py` (DeviceProfile), `http/tls_router.py` (TLSProfile) | 两套配置高度重复但硬编码独立。一个改了另一个没改就会产生 TLS/UA 不一致 |
| 13 | **`consistency.py` 的 Mac Firefox Profile 触发了 `validate()` 的 macOS GPU 检查** | `consistency.py` | Firefox 的 GPU renderer 写的是 NVIDIA 但 platform 是 MacIntel，导致 validate 报错 |

### P3 — 低影响

| # | 问题 | 涉及文件 | 影响 |
|---|------|---------|------|
| 14 | PCG 随机生成器使用 BigInt 精度损失 | `consistency.py` | 仅在 canvas 噪声中添加，不影响主逻辑 |
| 15 | `ConnectionReuseManager` 端口永不回收 | `http/connection_pool.py` | 长时间运行后端口溢出或被占用 |
| 16 | `subresource.py` 中 `asyncio.sleep(extra)` 的 6-9 秒空闲等待 | `engines/subresource.py` | 效率损失但非功能性 bug |
| 17 | `session_gap` 中 `page_count` 从 0 开始未减 1 | `behavior/timing.py` | 第一页 gap 为 1.0s，正确；但 `page_count` 与 `session_gap` 内的幂次增长语义有混淆 |

---

## 结构性问题总结

### 1. 目录结构不匹配
审查清单假设了 `fingerprint/` + `evasion/` 的分离结构，但实际代码按功能域组织（`behavior/`、`http/`、`engines/`、`utils/`）。需要决定是否：
- A) 实际重命名/迁移文件以匹配审查清单
- B) 更新审查清单以匹配实际代码结构

**推荐 B**，因为当前按功能域组织其实更清晰。

### 2. 指纹注入的"孤岛"问题
`consistency.py` 定义了一套完整的 6 层设备指纹注入系统，但没有被任何引擎调用。同时 `patched.py` 自己写了另一套 `_STEALTH_JS`。需要：
- 将 `PatchedEngine` 改为使用 `consistency.py` 的 `DeviceProfile.cdp_inject_script()`
- 将 `CloakedEngine.launch()` 也添加 CDP 级别的指纹注入（尽管有 CloakBrowser 底层，追加 JS 注入无害且增强防御）
- CamoufoxEngine（Firefox）不适合 JS 注入方式，保持 Firefox 原生 preference 配置

### 3. 指纹库规模不足
- **5 个设备配置文件**（win_chrome_124, win_chrome_131, mac_chrome_124, win_firefox_124, mac_safari_17）
- 缺少：Linux Chrome、Linux Firefox、Windows Edge、Chrome Mobile、Safari iOS
- 缺少不同屏幕尺寸、不同 GPU 型号、不同内存配置
- Windows 所有配置都是单一 GPU（RTX 3060），未覆盖 Intel UHD、AMD Radeon

---

## 总结

ApexCrawler 的反检测模块有着**优秀的设计理念和完整的多层防御架构**，但**实现和集成尚未完成**：

- **✅ 优势**：6 层指纹维度覆盖齐全（WebDriver→Navigator→Screen→WebGL→Canvas→Audio→Timezone）；真实 WebGL renderer 字符串可用；被动信号模拟合理质量高（Scroll depth power-law 分布、Mouse heat-map 区域采样）；Timing scheduler 模型科学（lognormal dwell + 时段 multiplier）。
- **❌ 劣势**：核心注入代码与引擎系统脱节；两套重复的 TLS/Profile 配置；多个审查项文件不存在；WebRTC 泄漏在 Chromium 引擎中未防御；DNS cache 阻塞异步事件循环；子资源路由模式未清理。
- **⚠️ 风险**：认为部署了 6 层验证但实际上只有 PatchedEngine 的 3-4 层注入有效；TLS 配置不一致可能导致 JA4/UA 不匹配被服务端检测；在 Canvas/Audio 中注入确定性噪声而不是真实设备特有 fingerprint 可能导致 fingerprint 库（如 FingerprintJS Pro）通过更长时检测到噪声模式。

**需要立即解决的行动项**：
1. 将 `cdp_inject_script()` 链接到 `PatchedEngine` 和 `CloakedEngine`
2. 统一 TLSProfile 和 DeviceProfile 数据源（合并或明确映射关系）
3. 为所有 Chromium 引擎添加 WebRTC IP 泄漏防护（`--force-webrtc-ip-handling-policy=disable_non_proxied_udp` 或 JS 注入）
4. 扩展设备配置文件至 12+ 个，覆盖不同 OS、浏览器、屏幕尺寸
5. 修正 `inject_dns_prefetch` 的语法错误
