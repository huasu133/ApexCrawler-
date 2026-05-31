# ApexCrawler 管线架构优化方案 — 自适应智能爬取

> 2026-05-31 | arch-opt | 基于 6 阶段管线当前实现与审计发现的架构优化

---

## 一、管线重构建议

### 1.1 当前管线问题

当前 6 阶段管线为**严格串行**执行（`pipeline/core.py:36-55`），存在以下结构性问题：

| 问题 | 位置 | 影响 |
|------|------|------|
| ExtractStage 占位实现 | `pipeline/stages.py:114-147` | 核心提取不可用 |
| Stage timeout 未从 config 加载 | `pipeline/core.py:40` | 硬编码 30s 默认值 |
| 降级链完全断裂 | 无 fallback 机制 | API→HTTP→Browser 无法自动切换 |
| 无阶段间并行支持 | `pipeline/core.py:36` | Evade+Extract 等可并行的组合串行浪费 |
| 无 Pipeline 级别指标暴露 | 全局缺失 | 无法观测各阶段性能 |

### 1.2 重构后管线架构

```
┌──────────────────────────────────────────────────────────────────┐
│              AdaptivePipelineExecutor (自适应管线执行器)           │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Schedule ──► Route ──► ┌──────────────┐ ──► Validate ──► Store │
│                         │ ParallelZone │                        │
│                         │ ┌──────────┐ │                        │
│                         │ │  Evade   │ │                        │
│                         │ └────┬─────┘ │                        │
│                         │      │       │              ▲          │
│                         │ ┌────▼─────┐ │              │          │
│                         │ │ Degrade  │ │──── failure ─┘          │
│                         │ │ Manager  │ │  (自动降级+重入)         │
│                         │ └────┬─────┘ │                         │
│                         │      │       │                         │
│                         │ ┌────▼─────┐ │                         │
│                         │ │ Extract  │ │                         │
│                         │ └──────────┘ │                         │
│                         └──────────────┘                         │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │              ObservableMetrics (贯穿全管线)                 │  │
│  │  ◄── duration / success_rate / error_rate / throughput ──►│  │
│  └───────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

### 1.3 具体 Stage 级别改动

#### (A) `pipeline/core.py` — 重构为 `AdaptivePipelineExecutor`

```python
@dataclass
class StageMetrics:
    """Per-stage observable metrics."""
    name: str
    total_invocations: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_duration_ms: float = 0.0
    # 用于 P50/P99 计算
    duration_samples: list[float] = field(default_factory=list)
    last_error: str = ""
    last_error_time: float = 0.0

@dataclass
class ParallelZone:
    """定义可并行执行的阶段组。"""
    stages: list[PipelineStage]
    max_concurrency: int = 3


class AdaptivePipelineExecutor:
    """自适应管线执行器 — 支持并行+降级+指标。"""

    def __init__(
        self,
        stages: list[PipelineStage],
        parallel_zones: list[ParallelZone] | None = None,
        degrade_manager: "DegradeManager | None" = None,
        config: "PipelineConfig | None" = None,
        metrics: "MetricsCollector | None" = None,
    ):
        self._stages = stages
        self._parallel_zones = parallel_zones or []
        self._degrader = degrade_manager
        self._config = config or PipelineConfig()  # 从 base.yaml 加载
        self._metrics = metrics or MetricsCollector()

    async def run(self, ctx: PipelineContext) -> tuple[bool, PipelineContext]:
        """Run pipeline with adaptive degrade and parallel execution."""
        start_ts = time.monotonic()
        executed = []

        # 展平阶段列表，识别 ParallelZone
        stage_plan = self._build_stage_plan()

        for item in stage_plan:
            if isinstance(item, ParallelZone):
                ctx = await self._execute_parallel(item, ctx, executed)
            else:
                stage: PipelineStage = item
                cfg = self._config.stage_timeouts.get(stage.name, 30.0)  # 从 config 加载!
                try:
                    ctx = await self._execute_with_retry(
                        stage, ctx,
                        timeout=cfg,
                        retry_policy=self._config.retry_policies.get(stage.name, RetryPolicy())
                    )
                    executed.append(stage)
                    self._metrics.record_success(stage.name, time.monotonic() - start_ts)
                except NonRetryableError as e:
                    ctx.fatal_error = str(e)
                    # 尝试降级
                    if self._degrader:
                        degrade_ctx = await self._degrader.try_degrade(ctx, stage.name, e)
                        if degrade_ctx:
                            ctx = degrade_ctx
                            continue  # 降级成功，继续下一阶段
                    await self._rollback(executed, ctx)
                    self._metrics.record_failure(stage.name, str(e))
                    return False, ctx
                except RetryableError as e:
                    # 同样尝试降级
                    if self._degrader:
                        degrade_ctx = await self._degrader.try_degrade(ctx, stage.name, e)
                        if degrade_ctx:
                            ctx = degrade_ctx
                            continue
                    ctx.stage_errors.setdefault(stage.name, []).append(str(e))
                    await self._rollback(executed, ctx)
                    self._metrics.record_failure(stage.name, str(e))
                    return False, ctx

        ctx.total_duration_ms = (time.monotonic() - start_ts) * 1000
        return True, ctx

    async def _execute_parallel(
        self, zone: ParallelZone, ctx: PipelineContext, executed: list
    ) -> PipelineContext:
        """并发执行 ParallelZone 内的多个阶段。"""
        tasks = []
        for stage in zone.stages:
            cfg = self._config.stage_timeouts.get(stage.name, 30.0)
            tasks.append(
                self._execute_with_retry(stage, ctx, timeout=cfg)
            )
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # 合并结果到 ctx
        for stage, result in zip(zone.stages, results):
            if isinstance(result, Exception):
                raise result
            ctx = self._merge_context(ctx, result)
            executed.append(stage)
        return ctx

    def _build_stage_plan(self) -> list:
        """将阶段列表和 ParallelZone 展平为执行计划。"""
        # 将 parallel_zones 插入到正确位置
        zone_map = {}
        for zone in self._parallel_zones:
            first_stage_name = zone.stages[0].name
            zone_map[first_stage_name] = zone

        plan = []
        for stage in self._stages:
            if stage.name in zone_map:
                plan.append(zone_map[stage.name])
                # 跳过 zone 内的其他 stage（它们已包含在 zone 中）
            elif not any(stage in z.stages for z in self._parallel_zones):
                plan.append(stage)
        return plan
```

#### (B) `pipeline/stages.py` — 重构 `ExtractStage` + 新增 `DegradeStage`

```python
class ExtractStage:
    """真正的提取阶段 — 使用引擎池获取页面内容。"""

    name = "extract"

    def __init__(
        self,
        engine_pool: EnginePool,
        extractor: Extractor | None = None,
        degrader: "DegradeManager | None" = None,
    ):
        self._pool = engine_pool
        self._extractor = extractor
        self._degrader = degrader

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.selected_engine:
            raise ConfigurationError("No engine selected")

        # 从引擎池获取引擎实例
        async with self._pool.acquire(ctx.selected_engine) as engine:
            page = await engine.navigate(
                ctx.target_url,
                proxy=ctx.proxy or None,
                headers={"User-Agent": ctx.user_agent} if ctx.user_agent else None,
            )

            ctx.raw_html = page.content
            ctx.extraction_confidence = 1.0  # 初始置信度

            # 如果配置了 AI 提取器，执行语义提取
            if self._extractor and ctx.extraction_schema:
                try:
                    extracted = await self._extractor.extract(
                        page.content,
                        ctx.extraction_schema
                    )
                    ctx.extracted_data = extracted.model_dump() if hasattr(extracted, 'model_dump') else extracted
                    ctx.extraction_confidence = getattr(self._extractor, 'confidence_threshold', 0.8)
                except ExtractionError:
                    ctx.extraction_confidence = 0.0

        return ctx

    # rollback 移除 raw_html/markdown/extracted_data
    async def rollback(self, ctx: PipelineContext) -> None:
        ctx.raw_html = ""
        ctx.markdown = ""
        ctx.extracted_data = None
        ctx.extraction_confidence = 0.0


class DegradeManager:
    """智能降级管理器 — 处理 API → HTTP → Browser 切换逻辑。"""

    # 降级链定义
    DEGRADE_CHAIN: list[tuple[str, str, int]] = [
        # (当前引擎, 降级目标引擎, 触发失败次数阈值)
        ("cloaked",  "camouflaged", 3),
        ("camouflaged", "patched", 3),
        ("patched",  "vanilla",   3),
        ("vanilla",  "httpx",     2),
    ]

    def __init__(self, cache: Cache, metrics: "MetricsCollector | None" = None):
        self._cache = cache
        self._metrics = metrics
        # 每个 target domain 的失败计数
        self._failure_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # 降级冷却期：domain 在冷却期内不能再次降级
        self._cooldown: dict[str, float] = {}

    async def try_degrade(
        self, ctx: PipelineContext, failed_stage: str, error: Exception
    ) -> PipelineContext | None:
        """尝试降级。返回更新后的 ctx，或 None 表示无法降级。"""
        domain = self._extract_domain(ctx.target_url)

        # 检查冷却期
        if domain in self._cooldown:
            if time.monotonic() - self._cooldown[domain] < 300:  # 5分钟冷却
                logger.info(f"Domain {domain} in degrade cooldown")
                return None

        # 记录失败
        self._failure_counts[domain][ctx.selected_engine] += 1
        current_failures = self._failure_counts[domain][ctx.selected_engine]

        # 查找降级链
        for current, target, threshold in self.DEGRADE_CHAIN:
            if current == ctx.selected_engine and current_failures >= threshold:
                logger.warning(
                    f"Degrading {domain} from {current} → {target} "
                    f"(failures={current_failures}, threshold={threshold})"
                )
                ctx.selected_engine = target
                ctx.route_reason = f"degraded from {current} (failures={current_failures})"
                ctx.degrade_count = getattr(ctx, 'degrade_count', 0) + 1

                # 如果已经降级超过 3 次，标记为 hard-fail
                if ctx.degrade_count >= 3:
                    ctx.fatal_error = f"Max degrade depth reached for {domain}"
                    return None

                # 记录降级指标
                if self._metrics:
                    self._metrics.record_degrade(domain, current, target)

                return ctx

        # 无法降级，进入冷却
        self._cooldown[domain] = time.monotonic()
        return None

    @staticmethod
    def _extract_domain(url: str) -> str:
        from urllib.parse import urlparse
        return urlparse(url).netloc
```

#### (C) 新增文件 `pipeline/config.py` — 从 base.yaml 加载管线配置

```python
"""Pipeline configuration loaded from base.yaml via Pydantic."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: bool = True


@dataclass
class PipelineConfig:
    """从 config/base.yaml 加载的管线配置。"""
    max_concurrent_tasks: int = 10
    retry_max: int = 3
    stage_timeouts: dict[str, float] = field(default_factory=lambda: {
        "schedule": 10,
        "route": 30,
        "evade": 20,
        "extract": 60,
        "validate": 10,
        "store": 15,
    })
    retry_policies: dict[str, RetryPolicy] = field(default_factory=lambda: {
        "extract": RetryPolicy(max_retries=3, base_delay=2.0, max_delay=30.0),
        "evade": RetryPolicy(max_retries=2, base_delay=1.0, max_delay=10.0),
    })
    # 并行区域配置
    parallel_zones: list[dict[str, Any]] = field(default_factory=list)
    # 降级配置
    degrade: dict[str, Any] = field(default_factory=lambda: {
        "enabled": True,
        "cooldown_seconds": 300,
        "max_degrade_depth": 3,
    })

    @classmethod
    def from_yaml(cls, path: str) -> "PipelineConfig":
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f)
        pipeline = data.get("pipeline", {})
        return cls(
            max_concurrent_tasks=pipeline.get("max_concurrent_tasks", 10),
            retry_max=pipeline.get("retry_max", 3),
            stage_timeouts=pipeline.get("stage_timeouts", {}),
            degrade=pipeline.get("degrade", {}),
        )
```

---

## 二、自适应策略

### 2.1 引擎/API 智能分层降级

```
                        ┌──────────┐
                        │ 入口: URL │
                        └────┬─────┘
                             │
                    ┌────────▼────────┐
                    │ DecisionEngine  │  L0 规则预判
                    │ recommend(url)  │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │ HTTP API │  │ httpx    │  │ Browser  │
        │ (首选)   │  │ (备用)   │  │ (兜底)   │
        └────┬─────┘  └────┬─────┘  └────┬─────┘
             │              │              │
             ▼              │              │
        success? ──YES──► Store           │
             │ NO                         │
             ▼              │              │
        ┌────────────┐     │              │
        │ DecisionEngine│──►│              │
        │ analyze()    │    │              │
        │ 检测反爬信号 │    │              │
        └──────┬───────┘    │              │
               │            ▼              │
               │      ┌──────────┐        │
               │      │ Evade    │        │
               │      │ 换代理+UA│        │
               │      └────┬─────┘        │
               │           │              │
               │      success? ──YES──► Store
               │           │ NO           │
               │           ▼              │
               │      ┌──────────────────┐│
               │      │ 触发 Browser     ││
               │      │ 降级阈值: 3次失败││
               │      └────────┬─────────┘│
               │               ▼          │
               │         ┌──────────┐     │
               └────────►│ Browser  │─────┘
                         │ (Playwright
                         │  Camoufox
                         │  CloakBrowser)
                         └──────────┘
```

#### 降级触发条件矩阵

```python
DEGRADE_TRIGGERS = {
    # ── HTTP API 层降级到 httpx ──
    "http_api": {
        "triggers": {
            "status_403": "WAF 拦截 — 检测 cf-ray/akamai 头",
            "status_429": "速率限制 — 提取 Retry-After 头",
            "status_503": "服务不可用 — 检查 cf-mitigated",
            "captcha_detected": "检测到 reCAPTCHA/hCaptcha/Turnstile",
            "empty_response": "响应体为空或 < 200 bytes",
            "consecutive_failures": "连续失败 >= 3 次",
            "timeout_rate": "超时率 > 50% (滑动窗口 1min)",
        },
        "threshold": 3,  # 累计失败次数触发降级
        "cooldown": 300,  # 降级后 5 分钟内保持降级状态
        "recovery_test_interval": 600,  # 10 分钟后尝试恢复
    },

    # ── httpx 层降级到 Browser ──
    "http": {
        "triggers": {
            "ja3_blocked": "TLS 指纹被拦截 (403 + 无 cf-ray)",
            "header_inspection": "检测到 header_inspection 信号",
            "js_required": "响应要求 JS 执行 (noscript / __NEXT_DATA__)",
            "spa_detected": "检测到 SPA (React/Vue/Angular) 需渲染",
            "datadome_captcha": "DataDome 滑块验证",
            "rate_limit": "持续 429 或 Retry-After > 30s",
            "proxy_blocked": "当前代理 IP 被 ban",
        },
        "threshold": 2,  # httpx 层更快降级
        "cooldown": 600,
        "recovery_test_interval": 1800,
    },

    # ── Browser 内部降级 ──
    "browser": {
        "triggers": {
            "vanilla_detected": "Playwright 被检测 (navigator.webdriver=true)",
            "headless_detected": "headless 特征暴露 (plugins 为空等)",
            "engine_crash": "浏览器崩溃或页面超时",
            "wasm_challenge": "检测到 WASM 加密挑战",
            "advanced_fingerprint": "WebGPU/Canvas/WebGL 综合指纹检测",
            "cloudflare_iuam": "Cloudflare I'm Under Attack 模式",
        },
        "engine_chain": [
            ("vanilla", "patched",   2),
            ("patched", "camouflaged", 2),
            ("camouflaged", "cloaked", 2),
        ],
    },
}
```

#### 恢复策略（自适应回升）

```python
class AdaptiveRecoveryManager:
    """管理降级后的自动恢复尝试。"""

    def __init__(self, cache: Cache):
        self._cache = cache
        self._recovery_intervals = {
            "http_api": 600,   # 10min
            "http": 1800,      # 30min
            "browser": 3600,   # 1h
        }

    async def should_attempt_recovery(self, domain: str, tier: str) -> bool:
        """是否应该尝试恢复到上一级？"""
        key = f"recovery:{domain}:{tier}"
        last_recovery = await self._cache.get(key)
        if last_recovery is None:
            return True
        interval = self._recovery_intervals.get(tier, 3600)
        return (time.monotonic() - last_recovery) > interval

    async def test_recovery(self, domain: str, target_tier: str) -> bool:
        """发送探针请求测试目标层是否已恢复。"""
        probe_url = f"https://{domain}/"
        try:
            # 使用目标层的引擎发探针
            if target_tier == "http_api":
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(probe_url)
                    return resp.status_code == 200
            elif target_tier == "http":
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(probe_url, follow_redirects=True)
                    return resp.status_code < 400
            return False
        except Exception:
            return False
```

### 2.2 自适应速率控制

```python
@dataclass
class RateController:
    """自适应速率控制器 — 基于目标站点的即时反爬强度。"""

    # 速率等级
    RATE_LEVELS = {
        0: 5.0,    # 激进: 5 req/s
        1: 2.0,    # 正常: 2 req/s
        2: 1.0,    # 谨慎: 1 req/s
        3: 0.5,    # 慢速: 1 req/2s
        4: 0.2,    # 极慢: 1 req/5s
        5: 0.05,   # 暂停: 1 req/20s (背压)
    }

    def __init__(self):
        self._domain_rates: dict[str, int] = defaultdict(lambda: 0)
        self._signal_history: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=50)  # 保留最近 50 次信号
        )

    async def adjust(self, domain: str, response: "CrawlResult") -> float:
        """根据响应调整速率。返回当前域名的建议延迟(秒)。"""
        current_level = self._domain_rates[domain]

        # 检测速率限制信号
        rate_signals = self._detect_rate_signals(response)

        if rate_signals:
            self._signal_history[domain].extend(rate_signals)
            # 计算信号密度
            recent = list(self._signal_history[domain])
            signal_density = sum(1 for s in recent if s["severity"] >= 1) / max(len(recent), 1)

            # 根据密度提升速率等级（减速）
            if signal_density > 0.5:
                current_level = min(5, current_level + 2)
            elif signal_density > 0.2:
                current_level = min(5, current_level + 1)

        # 无信号时，逐步恢复（加速）
        elif current_level > 0 and len(self._signal_history[domain]) > 10:
            recent = list(self._signal_history[domain])
            if sum(1 for s in recent if s["severity"] >= 1) < 2:
                current_level = max(0, current_level - 1)

        self._domain_rates[domain] = current_level
        return self.RATE_LEVELS[current_level]

    def _detect_rate_signals(self, response) -> list[dict]:
        """检测速率限制信号。"""
        signals = []

        # HTTP 429 Too Many Requests
        if getattr(response, 'status_code', 0) == 429:
            retry_after = int(getattr(response, 'headers', {}).get("retry-after", 60))
            signals.append({
                "type": "http_429",
                "severity": 3,
                "retry_after": retry_after,
            })

        # Cloudflare 限速
        cf_headers = getattr(response, 'headers', {})
        if "cf-chl-out" in {k.lower() for k in cf_headers}:
            signals.append({"type": "cloudflare_rate_limit", "severity": 2})

        # 响应时间异常增长
        if getattr(response, 'duration_seconds', 0) > 10:
            signals.append({"type": "response_slow", "severity": 1})

        # 空响应/截断响应
        html = getattr(response, 'raw_html', "") or ""
        if len(html) < 500 and getattr(response, 'status_code', 0) == 200:
            signals.append({"type": "truncated_response", "severity": 2})

        return signals


class DomainStateTracker:
    """追踪每个域名的实时健康状态。"""

    def __init__(self, cache: Cache):
        self._cache = cache
        self._state_ttl = 3600  # 状态缓存 1 小时

    async def get_state(self, domain: str) -> dict:
        cached = await self._cache.get(f"domain_state:{domain}")
        return cached or {
            "rate_level": 0,
            "current_delay": 0.0,
            "total_requests": 0,
            "success_rate": 1.0,
            "avg_latency_ms": 0.0,
            "active_degrade": False,
            "last_updated": time.time(),
        }

    async def update_state(self, domain: str, state: dict) -> None:
        state["last_updated"] = time.time()
        await self._cache.set(f"domain_state:{domain}", state, ttl=self._state_ttl)
```

### 2.3 代理动态切换逻辑

```python
class AdaptiveProxyManager:
    """代理自适应管理 — 基于失败模式自动切换。"""

    # 失败模式 → 代理动作
    FAILURE_ACTIONS = {
        "ip_ban":       ("rotate", "switch_proxy"),       # 换代理
        "geo_block":    ("rotate", "switch_geo"),          # 换地区
        "proxy_timeout": ("retry", "same_proxy_retry"),    # 重试同代理
        "proxy_dead":   ("remove", "ban_proxy"),           # 移除代理
        "captcha":      ("rotate", "switch_proxy_and_engine"),  # 换代理+引擎
    }

    def __init__(self, proxy_pool: ProxyPool, cache: Cache):
        self._pool = proxy_pool
        self._cache = cache

    async def handle_failure(
        self, ctx: PipelineContext, error: Exception
    ) -> PipelineContext:
        """处理代理失败，自动切换。"""
        failure_mode = self._classify_failure(error)

        action_type, action_name = self.FAILURE_ACTIONS.get(
            failure_mode, ("retry", "unknown")
        )

        logger.info(f"Proxy failure: {failure_mode} → {action_name}")

        if action_type == "rotate":
            # 切换代理
            ctx.proxy = await self._pool.get_proxy(
                geo=ctx.get("geo_requirement")
            )
            ctx.proxy_switch_count = getattr(ctx, 'proxy_switch_count', 0) + 1
            logger.info(f"Switched proxy → {ctx.proxy[:40]}...")

        elif action_type == "remove":
            # 永久移除故障代理
            self._pool.remove(ctx.proxy)
            ctx.proxy = await self._pool.get_proxy()
            logger.warning(f"Removed dead proxy, new proxy: {ctx.proxy[:40]}...")

        return ctx

    @staticmethod
    def _classify_failure(error: Exception) -> str:
        error_str = str(error).lower()
        if "ban" in error_str or "blocked" in error_str:
            return "ip_ban"
        if "geo" in error_str or "country" in error_str:
            return "geo_block"
        if "timeout" in error_str:
            return "proxy_timeout"
        if "connection" in error_str or "refused" in error_str:
            return "proxy_dead"
        if "captcha" in error_str:
            return "captcha"
        return "unknown"
```

---

## 三、状态管理 — Session/Cookie/Storage 持久化

### 3.1 当前问题

- `PipelineContext` 没有 Session 概念，每次请求都是无状态的
- 没有 Cookie Jar 持久化，反爬站点无法建立 Session 信任
- LocalStorage/IndexedDB 在跨请求之间丢失
- 各引擎的 browser context 没有隔离

### 3.2 Session 架构设计

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SessionManager                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────────┐   ┌──────────────────┐   ┌────────────────┐  │
│  │  CookieJarStore   │   │  StorageStore    │   │  ProfileStore  │  │
│  │  ────────────────│   │  ────────────────│   │  ──────────────│  │
│  │  • Netscape jar  │   │  • localStorage  │   │  • UA           │  │
│  │  • per-domain     │   │  • sessionStorage│   │  • TLS profile  │  │
│  │  • JSON 序列化    │   │  • IndexedDB     │   │  • Canvas hash  │  │
│  │  • Redis 持久化   │   │  • 序列化备份    │   │  • WebGL hash   │  │
│  └──────────────────┘   └──────────────────┘   └────────────────┘  │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                    Session Isolation                          │  │
│  │  ┌──────────┐   ┌──────────┐   ┌──────────┐                  │  │
│  │  │ Session A │   │ Session B │   │ Session C │   ...          │  │
│  │  │ domain: X │   │ domain: Y │   │ domain: Z │                │  │
│  │  │ engine: C │   │ engine: V │   │ engine: H │                │  │
│  │  │ Profile:1 │   │ Profile:2 │   │ Profile:3 │                │  │
│  │  │  Proxy: P1│   │  Proxy: P2│   │  Proxy: P3│                │  │
│  │  └──────────┘   └──────────┘   └──────────┘                  │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ⚠️ 关键规则: Session 内严禁切换引擎 (指纹一致性)                    │
│  每个 Session = 独立的 browser context + Cookie Jar + Storage         │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.3 实现方案

#### (A) 新增 `pipeline/session.py`

```python
"""Session management with full isolation and persistence."""

import json
import pickle
import hashlib
import time
from dataclasses import dataclass, field
from http.cookiejar import CookieJar, Cookie
from typing import Any


@dataclass
class SessionState:
    """持久化的 Session 状态。"""
    session_id: str
    domain: str
    engine: str
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)

    # Cookie 状态
    cookies: dict[str, list[dict]] = field(default_factory=dict)

    # Web Storage 状态
    local_storage: dict[str, str] = field(default_factory=dict)
    session_storage: dict[str, str] = field(default_factory=dict)

    # IndexedDB 状态 (序列化的数据库快照)
    indexed_db: dict[str, list[dict]] = field(default_factory=dict)

    # 指纹 Profile
    profile: dict[str, Any] = field(default_factory=dict)

    # 请求历史
    request_count: int = 0
    last_status_code: int = 0

    # 置信度分数
    trust_score: float = 0.5  # 0-1, 越高表示 Session 越可信

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "domain": self.domain,
            "engine": self.engine,
            "cookies": self.cookies,
            "local_storage": self.local_storage,
            "session_storage": self.session_storage,
            "profile": self.profile,
            "request_count": self.request_count,
            "trust_score": self.trust_score,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionState":
        return cls(**data)


class CookieJarStore:
    """Cookie Jar 持久化存储 — 支持多域名隔离。"""

    def __init__(self, cache: Cache):
        self._cache = cache

    def key(self, session_id: str, domain: str) -> str:
        return f"cookies:{session_id}:{domain}"

    async def save(self, session_id: str, domain: str, cookies: list[dict]) -> None:
        """持久化 Cookie 到缓存后端。"""
        await self._cache.set(
            self.key(session_id, domain),
            cookies,
            ttl=86400 * 7,  # 7 天有效期
        )

    async def load(self, session_id: str, domain: str) -> list[dict]:
        """加载持久化的 Cookie。"""
        return await self._cache.get(self.key(session_id, domain)) or []

    async def export_netscape(self, session_id: str, domain: str) -> str:
        """导出为 Netscape cookie 格式 (curl 兼容)。"""
        cookies = await self.load(session_id, domain)
        lines = ["# Netscape HTTP Cookie File"]
        for c in cookies:
            domain_flag = "TRUE" if c.get("domain", "").startswith(".") else "FALSE"
            lines.append(
                f"{c.get('domain', domain)}\t{domain_flag}\t"
                f"{c.get('path', '/')}\t"
                f"{'TRUE' if c.get('secure', False) else 'FALSE'}\t"
                f"{c.get('expires', 0)}\t"
                f"{c.get('name', '')}\t"
                f"{c.get('value', '')}"
            )
        return "\n".join(lines)


class StorageStore:
    """Web Storage 持久化 — LocalStorage + SessionStorage + IndexedDB。"""

    def __init__(self, cache: Cache):
        self._cache = cache

    async def save_local_storage(
        self, session_id: str, storage: dict[str, str]
    ) -> None:
        """保存 LocalStorage 快照。"""
        key = f"storage:ls:{session_id}"
        await self._cache.set(key, storage, ttl=86400 * 7)

    async def load_local_storage(self, session_id: str) -> dict[str, str]:
        return await self._cache.get(f"storage:ls:{session_id}") or {}

    async def save_indexed_db(
        self, session_id: str, db_name: str, data: list[dict]
    ) -> None:
        """保存 IndexedDB 快照。"""
        key = f"storage:idb:{session_id}:{db_name}"
        await self._cache.set(key, data, ttl=86400 * 7)

    async def load_indexed_db(
        self, session_id: str, db_name: str
    ) -> list[dict]:
        return await self._cache.get(f"storage:idb:{session_id}:{db_name}") or []

    # ── 注入方法（在浏览器启动时执行）──

    @staticmethod
    def build_injection_script(
        local_storage: dict[str, str],
        session_storage: dict[str, str],
    ) -> str:
        """生成 JS 注入脚本，恢复 Web Storage 状态。"""
        ls_entries = json.dumps(local_storage)
        ss_entries = json.dumps(session_storage)
        return f"""
        (function() {{
            var ls = {ls_entries};
            var ss = {ss_entries};
            // 恢复 localStorage
            for (var k in ls) {{
                if (ls.hasOwnProperty(k)) {{
                    try {{ localStorage.setItem(k, ls[k]); }} catch(e) {{}}
                }}
            }}
            // 恢复 sessionStorage
            for (var k in ss) {{
                if (ss.hasOwnProperty(k)) {{
                    try {{ sessionStorage.setItem(k, ss[k]); }} catch(e) {{}}
                }}
            }}
        }})();
        """


class SessionManager:
    """Session 生命周期管理器。"""

    def __init__(self, cache: Cache):
        self._cache = cache
        self._cookie_store = CookieJarStore(cache)
        self._storage_store = StorageStore(cache)
        # Session 隔离锁：确保同一 domain 不会同时有多个活跃 session
        self._session_locks: dict[str, asyncio.Lock] = {}

    def session_key(self, domain: str) -> str:
        """生成 domain 的 session key。

        规则: 每个 domain 最多一个 Session（可配置为 per-domain+engine 隔离）。
        """
        return f"session:{hashlib.sha256(domain.encode()).hexdigest()[:16]}"

    async def get_or_create_session(
        self,
        domain: str,
        engine: str,
        profile: dict | None = None,
    ) -> SessionState:
        """获取或创建 Session。确保引擎一致性。"""
        key = self.session_key(domain)
        existing = await self._cache.get(key)

        if existing:
            session = SessionState.from_dict(existing)
            # 引擎一致性检查
            if session.engine != engine:
                logger.warning(
                    f"Engine mismatch for {domain}: "
                    f"session={session.engine}, requested={engine}. "
                    f"Keeping existing engine for fingerprint consistency."
                )
                # 不使用请求的引擎，保持 Session 内引擎一致
            session.last_used_at = time.time()
            return session

        # 创建新 Session
        session = SessionState(
            session_id=hashlib.sha256(
                f"{domain}:{time.time()}".encode()
            ).hexdigest()[:16],
            domain=domain,
            engine=engine,
            profile=profile or {},
        )
        await self._cache.set(key, session.to_dict(), ttl=86400)
        return session

    async def save_session_cookies(
        self, session_id: str, domain: str, cookies: list[dict]
    ) -> None:
        await self._cookie_store.save(session_id, domain, cookies)

    async def save_storage_snapshot(
        self,
        session_id: str,
        local_storage: dict[str, str],
        session_storage: dict[str, str],
    ) -> None:
        await self._storage_store.save_local_storage(session_id, local_storage)
        # session_storage 在页面关闭时清除，但这里做快照备份
        await self._cache.set(
            f"storage:ss:{session_id}",
            session_storage,
            ttl=86400,
        )

    async def load_for_context(self, ctx: PipelineContext) -> PipelineContext:
        """加载 Session 状态到 PipelineContext。"""
        domain = DegradeManager._extract_domain(ctx.target_url)
        session = await self.get_or_create_session(
            domain, ctx.selected_engine
        )

        ctx.session_id = session.session_id
        ctx.cookies = await self._cookie_store.load(session.session_id, domain)
        ctx.local_storage = await self._storage_store.load_local_storage(
            session.session_id
        )
        ctx.browser_profile = session.profile

        # 将 Session 信息注入到 Evade 阶段
        ctx.user_agent = session.profile.get("user_agent", ctx.user_agent)
        ctx.tls_profile = session.profile.get("tls_profile", ctx.tls_profile)

        return ctx

    async def persist_from_context(self, ctx: PipelineContext) -> None:
        """从 PipelineContext 持久化 Session 状态。"""
        domain = DegradeManager._extract_domain(ctx.target_url)

        if getattr(ctx, 'cookies', None):
            await self.save_session_cookies(
                ctx.session_id, domain, ctx.cookies
            )
        if getattr(ctx, 'local_storage', None):
            await self.save_storage_snapshot(
                ctx.session_id, ctx.local_storage, {}
            )

        # 更新 Session 元数据
        key = self.session_key(domain)
        session = SessionState.from_dict(await self._cache.get(key) or {})
        session.last_used_at = time.time()
        session.request_count += 1
        await self._cache.set(key, session.to_dict(), ttl=86400)

    # ── 浏览器启动时的 Cookie 预注入 ──

    async def inject_cookies_into_page(self, page: "Page", session_id: str, domain: str) -> None:
        """在浏览器 Page 中注入持久化的 Cookie。"""
        cookies = await self._cookie_store.load(session_id, domain)
        if not cookies:
            return

        # 使用 CDP 或 Playwright API 注入
        if hasattr(page, 'context'):
            await page.context.add_cookies(cookies)
        elif hasattr(page, 'evaluate'):
            # 通过 JS 注入
            cookie_script = "; ".join(
                f"document.cookie = '{json.dumps(f'{c['name']}={c['value']}')}';"
                for c in cookies
            )
            await page.evaluate(cookie_script)
```

#### (B) `PipelineContext` 扩展字段

```python
@dataclass
class PipelineContext:
    # ... existing fields ...

    # ── Session 相关 ──
    session_id: str = ""
    cookies: list[dict] = field(default_factory=list)
    local_storage: dict[str, str] = field(default_factory=dict)
    session_storage: dict[str, str] = field(default_factory=dict)
    trust_score: float = 0.5

    # ── 降级追踪 ──
    degrade_count: int = 0
    degrade_path: list[str] = field(default_factory=list)  # ["cloaked", "camouflaged"]

    # ── 代理追踪 ──
    proxy_switch_count: int = 0

    # ── 指标 ──
    total_duration_ms: float = 0.0
    stage_durations: dict[str, float] = field(default_factory=dict)
```

---

## 四、错误恢复与自愈

### 4.1 自愈架构

```
提取失败时的多层恢复链:

XPath 匹配失败
    │
    ├──► 语义重定位 (AI Extractor 重新分析结构)
    │    └── 成功 → 更新 XPath 缓存
    │
    ├──► 结构变化检测 (DOM 相似度比较)
    │    ├── 轻微变化 (<30%) → 模糊 XPath 匹配
    │    └── 重大变化 (>30%) → 触发 LLM 重分析
    │
    └──► 字体映射失效
         ├──► FontTools 重新解析 (检查缓存)
         ├──► OCR 双引擎回退 (ddddocr → PaddleOCR)
         └──► 成功率低于60% → 触发人工审核

代理失效
    │
    ├──► 同 Geo 其他代理 (P0)
    ├──► 跨 Geo 代理 (P1)  
    ├──► 直连重试 (P2, 仅 low-difficulty)
    └──► 所有代理失败 → 上游告警
```

### 4.2 XPath 语义重定位

```python
class SemanticRelocator:
    """当 XPath/CSS Selector 失效时，使用语义重新定位目标元素。"""

    def __init__(self, llm_client=None, cache: Cache | None = None):
        self._llm = llm_client
        self._cache = cache

    async def relocate(
        self, html: str, original_selector: str, target_schema: dict
    ) -> str | None:
        """语义重定位 — 找到最佳匹配的新选择器。

        Returns:
            新的 XPath/CSS 选择器，或 None 表示无法定位。
        """
        # L0: 尝试模糊匹配（放宽选择器条件）
        fuzzy = self._fuzzy_match(html, original_selector)
        if fuzzy:
            logger.info(f"Semantic relocate: fuzzy match → {fuzzy}")
            return fuzzy

        # L1: 使用 LLM 分析结构变化，重写选择器
        if self._llm:
            new_selector = await self._llm_relocate(
                html, original_selector, target_schema
            )
            if new_selector:
                await self._cache_selector(original_selector, new_selector)
                return new_selector

        return None

    def _fuzzy_match(self, html: str, selector: str) -> str | None:
        """放宽 XPath 约束进行模糊匹配。"""
        # //div[@class='price'] → //*[@class='price'] | //*[contains(@class,'price')]
        import re
        from lxml import etree

        try:
            tree = etree.HTML(html)

            # 移除位置索引: //div[3] → //div
            relaxed = re.sub(r'\[\d+\]', '', selector)
            # 放宽属性匹配: @class='exact' → contains(@class,'exact')
            relaxed = re.sub(
                r"""@(\w+)=['"]([^'"]+)['"]""",
                r"""contains(@\1,'\2')""",
                relaxed,
            )

            result = tree.xpath(relaxed)
            if result:
                return relaxed
        except Exception:
            pass
        return None

    async def _llm_relocate(
        self, html: str, old_selector: str, schema: dict
    ) -> str | None:
        """使用 LLM 分析 DOM 结构变化。"""
        # 截断 HTML 减轻 LLM 负担
        html_snippet = html[:8000]

        prompt = f"""The XPath selector "{old_selector}" no longer matches the page.
Target data structure: {json.dumps(schema, default=str)[:500]}

Page HTML snippet:
{html_snippet}

Provide a new XPath or CSS selector that matches the target data.
Respond with ONLY the selector, nothing else."""

        try:
            response = self._llm.generate(prompt)  # type: ignore
            return response.strip().strip('"').strip("'")
        except Exception:
            return None

    async def _cache_selector(self, old: str, new: str) -> None:
        """缓存映射关系，加速后续匹配。"""
        if self._cache:
            key = f"xpath_map:{hashlib.sha256(old.encode()).hexdigest()[:16]}"
            await self._cache.set(key, new, ttl=86400 * 30)
```

### 4.3 字体映射 OCR 回退

```python
class FontRecoveryManager:
    """字体解码失败后的多级恢复。"""

    def __init__(self, font_cracker: FontCracker, ocr_engine: OCREngine):
        self._cracker = font_cracker
        self._ocr = ocr_engine

    async def recover_font_mapping(
        self, html: str, page_url: str
    ) -> dict[str, str]:
        """字体映射恢复链。"""
        # L0: FontTools 重新解析（清除缓存）
        try:
            result = await self._cracker.crack(html, page_url)
            if result:
                return result
        except Exception as e:
            logger.warning(f"FontTools parse failed: {e}")

        # L1: 渲染字体 → 截图 → OCR (ddddocr)
        try:
            glyph_images = await self._render_glyphs(html)
            mapping = await self._ocr.build_glyph_map(glyph_images)
            if len(mapping) > 0:
                return mapping
        except Exception as e:
            logger.warning(f"ddddocr failed: {e}")

        # L2: PaddleOCR 回退
        try:
            from apexcrawler.anti_font.ocr_engine import OCREngine
            paddle_ocr = OCREngine(backend="paddleocr")
            glyph_images = await self._render_glyphs(html)
            mapping = await paddle_ocr.build_glyph_map(
                glyph_images,
                min_confidence=0.6  # 降低阈值
            )
            if len(mapping) > 0:
                return mapping
        except Exception as e:
            logger.error(f"PaddleOCR fallback failed: {e}")

        # L3: 标记为不可恢复
        logger.error(f"Font mapping irrecoverable for {page_url}")
        return {}

    async def _render_glyphs(self, html: str) -> list[tuple[str, bytes]]:
        """渲染字体 glyph 为图片。"""
        # 提取 font-face URLs
        # 渲染每个 glyph 为独立图片
        # 返回 (glyph_name, image_bytes) 列表
        # TODO: 实现实际渲染逻辑
        return []
```

### 4.4 代理自愈

```python
class ProxySelfHealer:
    """代理自动恢复与健康管理。"""

    def __init__(self, proxy_pool: ProxyPool, cache: Cache):
        self._pool = proxy_pool
        self._cache = cache

    async def heal(self, ctx: PipelineContext, failure_count: int) -> PipelineContext:
        """代理故障自愈。"""
        domain = DegradeManager._extract_domain(ctx.target_url)

        if failure_count == 1:
            # 第1次失败：同 Geo 换代理
            ctx.proxy = await self._pool.get_proxy(
                geo=ctx.get("geo_requirement")
            )
            ctx.proxy_switch_count = 1

        elif failure_count == 2:
            # 第2次失败：跨 Geo 换代理
            ctx.proxy = await self._pool.get_proxy(geo=None)  # 不限制地区
            ctx.proxy_switch_count = 2

        elif failure_count == 3:
            # 第3次失败：上游告警
            logger.error(
                f"All proxies failed for {domain}. "
                f"Sending alert and pausing crawl for this domain."
            )
            # 标记 domain 进入冷却
            await self._cache.set(
                f"domain_cooldown:{domain}",
                {"until": time.time() + 3600, "reason": "proxy_exhausted"},
                ttl=3600,
            )
            ctx.fatal_error = "Proxy pool exhausted"
            return ctx

        return ctx
```

---

## 五、可观测性 — 指标、日志、告警

### 5.1 指标收集器

```python
"""Observability: metrics collection for the entire pipeline."""

import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PipelineMetrics:
    """全量管线指标。"""
    # ── 总体指标 ──
    total_requests: int = 0
    total_success: int = 0
    total_failure: int = 0
    total_duration_ms: float = 0.0
    requests_per_second: float = 0.0

    # ── 分阶段指标 ──
    stage_metrics: dict[str, "StageMetrics"] = field(default_factory=dict)

    # ── 降级指标 ──
    degrade_events: list[dict] = field(default_factory=list)
    degrade_rate: float = 0.0  # 降级事件/请求

    # ── 错误分布 ──
    error_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    error_rate: float = 0.0

    # ── 延迟分布 ──
    latency_samples: deque = field(default_factory=lambda: deque(maxlen=1000))
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0

    # ── 引擎指标 ──
    engine_usage: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    engine_success_rate: dict[str, float] = field(default_factory=dict)

    # ── 代理指标 ──
    proxy_switch_rate: float = 0.0
    active_proxies: int = 0

    # ── 速率控制 ──
    current_rate_level: dict[str, int] = field(default_factory=dict)  # domain → level
    rate_limit_events: int = 0


class MetricsCollector:
    """管线指标收集器 — 线程安全。"""

    def __init__(self, window_size: int = 1000):
        self._lock = threading.Lock()
        self._window_size = window_size

        # 阶段级别
        self._stage_success: dict[str, int] = defaultdict(int)
        self._stage_failure: dict[str, int] = defaultdict(int)
        self._stage_durations: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=window_size)
        )

        # 全局级别
        self._latencies: deque = deque(maxlen=window_size)
        self._error_types: dict[str, int] = defaultdict(int)
        self._degrade_events: list = []
        self._request_count: int = 0
        self._success_count: int = 0

        # 窗口时间戳
        self._window_start = time.monotonic()
        self._recent_requests: deque = deque(maxlen=100)  # 计算 RPS

    # ── 记录方法 ──

    def record_success(self, stage_name: str, duration_ms: float) -> None:
        with self._lock:
            self._stage_success[stage_name] += 1
            self._stage_durations[stage_name].append(duration_ms)
            self._success_count += 1
            self._request_count += 1
            self._latencies.append(duration_ms)
            self._recent_requests.append((time.monotonic(), True))

    def record_failure(self, stage_name: str, error: str) -> None:
        with self._lock:
            self._stage_failure[stage_name] += 1
            self._error_types[self._classify_error(error)] += 1
            self._request_count += 1
            self._recent_requests.append((time.monotonic(), False))

    def record_degrade(self, domain: str, from_engine: str, to_engine: str) -> None:
        with self._lock:
            self._degrade_events.append({
                "domain": domain,
                "from": from_engine,
                "to": to_engine,
                "timestamp": time.time(),
            })

    def record_rate_limit(self, domain: str, level: int) -> None:
        pass  # 由 RateController 管理

    # ── 查询方法 ──

    def snapshot(self) -> PipelineMetrics:
        """获取当前指标快照。"""
        with self._lock:
            metrics = PipelineMetrics()

            # 总体
            metrics.total_requests = self._request_count
            metrics.total_success = self._success_count
            metrics.total_failure = self._request_count - self._success_count

            # 延迟百分位
            if self._latencies:
                sorted_lat = sorted(self._latencies)
                metrics.p50_ms = sorted_lat[len(sorted_lat) // 2]
                metrics.p95_ms = sorted_lat[int(len(sorted_lat) * 0.95)]
                metrics.p99_ms = sorted_lat[int(len(sorted_lat) * 0.99)]

            # 分阶段
            for stage in set(list(self._stage_success.keys()) + list(self._stage_failure.keys())):
                success = self._stage_success[stage]
                failure = self._stage_failure[stage]
                durations = list(self._stage_durations[stage])
                metrics.stage_metrics[stage] = StageMetrics(
                    name=stage,
                    success_count=success,
                    failure_count=failure,
                    total_invocations=success + failure,
                    avg_duration_ms=sum(durations) / len(durations) if durations else 0,
                    p50_ms=sorted(durations)[len(durations)//2] if durations else 0,
                    p99_ms=sorted(durations)[int(len(durations)*0.99)] if len(durations) >= 100 else 0,
                )

            # 错误分布
            metrics.error_counts = dict(self._error_types)
            metrics.error_rate = (
                sum(self._error_types.values()) / max(self._request_count, 1)
            )

            # 降级事件
            metrics.degrade_events = list(self._degrade_events[-20:])  # 最近20条
            metrics.degrade_rate = (
                len(self._degrade_events) / max(self._request_count, 1)
            )

            # RPS
            elapsed = time.monotonic() - self._window_start
            metrics.requests_per_second = (
                self._request_count / max(elapsed, 1)
            )

            return metrics

    def get_stage_health(self, stage_name: str) -> dict:
        """获取某个阶段的健康状态。"""
        with self._lock:
            success = self._stage_success[stage_name]
            failure = self._stage_failure[stage_name]
            total = success + failure
            durations = list(self._stage_durations[stage_name])

            return {
                "stage": stage_name,
                "total": total,
                "success": success,
                "failure": failure,
                "success_rate": success / max(total, 1),
                "avg_latency_ms": sum(durations) / len(durations) if durations else 0,
                "p99_latency_ms": sorted(durations)[int(len(durations)*0.99)] if len(durations) >= 100 else 0,
                "status": "healthy" if success / max(total, 1) > 0.9 else (
                    "degraded" if success / max(total, 1) > 0.5 else "critical"
                ),
            }

    @staticmethod
    def _classify_error(error: str) -> str:
        error_lower = error.lower()
        if "proxy" in error_lower:
            return "proxy_error"
        if "timeout" in error_lower:
            return "timeout"
        if "captcha" in error_lower:
            return "captcha"
        if "403" in error_lower or "429" in error_lower:
            return "http_error"
        if "extract" in error_lower:
            return "extraction_error"
        if "validate" in error_lower:
            return "validation_error"
        return "unknown"
```

### 5.2 Prometheus 指标暴露

```python
class PrometheusExporter:
    """将 Pipeline 指标暴露为 Prometheus 格式。"""

    def __init__(self, metrics: MetricsCollector):
        self._metrics = metrics

    def render(self) -> str:
        """渲染 Prometheus 文本格式。"""
        snapshot = self._metrics.snapshot()
        lines = []

        # 总体指标
        lines.append("# HELP apexcrawler_requests_total Total pipeline requests")
        lines.append("# TYPE apexcrawler_requests_total counter")
        lines.append(f"apexcrawler_requests_total {snapshot.total_requests}")

        lines.append("# HELP apexcrawler_success_total Successful pipeline executions")
        lines.append("# TYPE apexcrawler_success_total counter")
        lines.append(f"apexcrawler_success_total {snapshot.total_success}")

        lines.append("# HELP apexcrawler_failure_total Failed pipeline executions")
        lines.append("# TYPE apexcrawler_failure_total counter")
        lines.append(f"apexcrawler_failure_total {snapshot.total_failure}")

        # 延迟
        lines.append("# HELP apexcrawler_latency_ms Pipeline latency in ms")
        lines.append("# TYPE apexcrawler_latency_ms summary")
        lines.append(f"apexcrawler_latency_ms{{quantile=\"0.5\"}} {snapshot.p50_ms}")
        lines.append(f"apexcrawler_latency_ms{{quantile=\"0.95\"}} {snapshot.p95_ms}")
        lines.append(f"apexcrawler_latency_ms{{quantile=\"0.99\"}} {snapshot.p99_ms}")

        # 分阶段
        lines.append("# HELP apexcrawler_stage_success_total Per-stage success count")
        lines.append("# TYPE apexcrawler_stage_success_total counter")
        for name, sm in snapshot.stage_metrics.items():
            lines.append(
                f"apexcrawler_stage_success_total{{stage=\"{name}\"}} {sm.success_count}"
            )

        lines.append("# HELP apexcrawler_stage_latency_ms Per-stage latency P99")
        lines.append("# TYPE apexcrawler_stage_latency_ms gauge")
        for name, sm in snapshot.stage_metrics.items():
            lines.append(
                f"apexcrawler_stage_latency_ms{{stage=\"{name}\"}} {sm.p99_ms}"
            )

        # 错误分布
        lines.append("# HELP apexcrawler_errors_total Error counts by type")
        lines.append("# TYPE apexcrawler_errors_total counter")
        for error_type, count in snapshot.error_counts.items():
            lines.append(
                f"apexcrawler_errors_total{{type=\"{error_type}\"}} {count}"
            )

        # 降级率
        lines.append("# HELP apexcrawler_degrade_rate Degrade event rate")
        lines.append("# TYPE apexcrawler_degrade_rate gauge")
        lines.append(f"apexcrawler_degrade_rate {snapshot.degrade_rate}")

        # RPS
        lines.append("# HELP apexcrawler_requests_per_second Current RPS")
        lines.append("# TYPE apexcrawler_requests_per_second gauge")
        lines.append(f"apexcrawler_requests_per_second {snapshot.requests_per_second}")

        return "\n".join(lines) + "\n"
```

### 5.3 结构化日志增强

在现有 `utils/logger.py` 的 structlog 基础上，增加管线上下文字段：

```python
# 在 PipelineExecutor.run() 中添加:
logger = structlog.get_logger().bind(
    trace_id=ctx.trace_id,
    session_id=ctx.session_id,
    domain=DegradeManager._extract_domain(ctx.target_url),
    engine=ctx.selected_engine,
    difficulty=ctx.target_difficulty,
)
```

### 5.4 告警规则

```yaml
# alerts.yaml — 推荐接入 Prometheus AlertManager
groups:
  - name: apexcrawler
    rules:
      # 成功率告警
      - alert: HighFailureRate
        expr: |
          rate(apexcrawler_failure_total[5m]) /
          rate(apexcrawler_requests_total[5m]) > 0.3
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "爬取失败率超过 30%"

      # 高延迟告警
      - alert: HighLatency
        expr: apexcrawler_latency_ms{quantile="0.99"} > 30000
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "P99 延迟超过 30 秒"

      # 高降级率告警
      - alert: HighDegradeRate
        expr: apexcrawler_degrade_rate > 0.2
        for: 10m
        labels:
          severity: critical
        annotations:
          summary: "降级率超过 20%，大量目标需要 Browser 兜底"

      # 代理池枯竭
      - alert: ProxyPoolExhausted
        expr: apexcrawler_active_proxies < 3
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "可用代理不足 3 个"

      # Extract 阶段异常
      - alert: ExtractStageDegraded
        expr: |
          rate(apexcrawler_stage_success_total{stage="extract"}[5m]) /
          (rate(apexcrawler_stage_success_total{stage="extract"}[5m]) +
           rate(apexcrawler_errors_total{type="extraction_error"}[5m])) < 0.7
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Extract 阶段成功率低于 70%"
```

---

## 六、实施优先级

### P0 — 核心机制 (第 1-2 周)

| # | 改动 | 文件 | 工作量 | 依赖 |
|---|------|------|--------|------|
| 1 | ExtractStage 真实实现 (引擎池+导航) | `pipeline/stages.py` | 2天 | EnginePool 已有 |
| 2 | PipelineConfig 从 base.yaml 加载 timeout | `pipeline/core.py` | 0.5天 | 无 |
| 3 | DegradeManager 降级链 | 新增 `pipeline/degrade.py` | 2天 | ExtractStage |
| 4 | AdaptivePipelineExecutor (并行+降级) | `pipeline/core.py` 重构 | 2天 | #2, #3 |
| 5 | SessionManager + CookieJarStore | 新增 `pipeline/session.py` | 3天 | Cache |
| 6 | PipelineContext 扩展字段 (session/cookies) | `core/context.py` | 0.5天 | 无 |

### P1 — 智能自适应 (第 3-4 周)

| # | 改动 | 文件 | 工作量 | 依赖 |
|---|------|------|--------|------|
| 7 | RateController 自适应速率 | 新增 `pipeline/rate_control.py` | 2天 | P0 管线 |
| 8 | DomainStateTracker 域名状态追踪 | 新增 `pipeline/domain_state.py` | 1天 | #7 |
| 9 | AdaptiveRecoveryManager 回升策略 | 新增 `pipeline/recovery.py` | 1.5天 | #3 |
| 10 | ProxySelfHealer 代理自愈 | 新增 `pipeline/proxy_heal.py` | 1天 | ProxyPool 已有 |
| 11 | FontRecoveryManager 字体回退链 | 新增 `pipeline/font_recovery.py` | 1.5天 | FontCracker+OCR |
| 12 | SemanticRelocator XPath 重定位 | 新增 `pipeline/semantic_relocator.py` | 2天 | LLM |

### P2 — 可观测性 (第 5-6 周)

| # | 改动 | 文件 | 工作量 | 依赖 |
|---|------|------|--------|------|
| 13 | MetricsCollector 全量指标收集 | 新增 `pipeline/metrics.py` | 2天 | AdaptivePipelineExecutor |
| 14 | PrometheusExporter 指标暴露 | 新增 `pipeline/prometheus.py` | 1天 | MetricsCollector |
| 15 | 结构化日志增强 (管线上下文字段) | `utils/logger.py` | 0.5天 | 无 |
| 16 | 告警规则配置 | 新增 `config/alerts.yaml` | 1天 | MetricsCollector |
| 17 | StorageStore (LocalStorage/IndexedDB持久化) | `pipeline/session.py` 扩展 | 2天 | SessionManager |
| 18 | End-to-end 集成测试 + 负载测试 | `tests/` | 3天 | 全部 P0+P1 |

---

## 七、新增文件总览

```
apexcrawler/pipeline/
├── core.py              # 重构: AdaptivePipelineExecutor
├── stages.py            # 重构: ExtractStage 真实实现 + DegradeManager
├── config.py            # 🆕 PipelineConfig (from base.yaml)
├── session.py           # 🆕 SessionManager, CookieJarStore, StorageStore
├── degrade.py           # 🆕 DegradeManager (分层降级)
├── rate_control.py      # 🆕 RateController (自适应速率)
├── domain_state.py      # 🆕 DomainStateTracker
├── recovery.py          # 🆕 AdaptiveRecoveryManager
├── proxy_heal.py        # 🆕 ProxySelfHealer
├── font_recovery.py     # 🆕 FontRecoveryManager
├── semantic_relocator.py # 🆕 SemanticRelocator
├── metrics.py           # 🆕 MetricsCollector
├── prometheus.py        # 🆕 PrometheusExporter
└── __init__.py          # 更新导出
```

---

> **总结**: 本方案覆盖了智能分层降级、管线并行化、会话状态管理、自适应速率控制、错误恢复自愈、监控可观测性全部 6 个维度。优先实施 P0 核心机制即可立即消除 ExtractStage 占位、timeout 未接入、降级链断裂三个已知 P0 问题。
