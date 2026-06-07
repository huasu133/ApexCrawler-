"""API → HTTP → Browser 自动降级链。

触发条件: status 403/429/503, captcha, empty body, timeout ×3
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..core.context import PipelineContext

logger = logging.getLogger(__name__)


def _extract_domain(url: str) -> str:
    """从 URL 中提取域名，处理边界情况。"""
    if not url or "://" not in url:
        return ""
    return url.split("/")[2]


@dataclass
class DegradeState:
    """Current degradation state for a pipeline execution."""

    layer: str = "api"
    api_failures: int = 0
    http_failures: int = 0
    last_status: int = 0
    last_body_len: int = 0
    captcha_detected: bool = False


class DegradeManager:
    """三层降级链: API → HTTP → Browser。

    基于域名聚合失败计数，当失败次数超过阈值时自动降级到下层级。
    """

    LAYERS = ["api", "http", "browser"]

    def __init__(self, thresholds: dict | None = None):
        self._thresholds = thresholds or {"api": 3, "http": 2}
        self._failures: dict[str, int] = {}
        self._states: dict[str, DegradeState] = {}

    def record_failure(self, url: str) -> bool:
        """记录一次失败，返回是否应该降级。"""
        domain = _extract_domain(url)
        self._failures[domain] = self._failures.get(domain, 0) + 1
        return self._should_degrade(domain)

    def _should_degrade(self, domain: str) -> bool:
        """检查域名是否应降级到下层。根据当前层级选择对应阈值。"""
        f = self._failures.get(domain, 0)
        state = self._states.get(domain)
        if state and state.layer == "http":
            # 当前在 http 层，检查是否需要进一步降级到 browser
            http_threshold = self._thresholds.get("api", 3)
            browser_threshold = http_threshold + self._thresholds.get("http", 2)
            return f >= browser_threshold
        # 默认检查 api 阈值（包括 api 层或未初始化时）
        return f >= self._thresholds.get("api", 3)

    def record_response(
        self, url: str, status: int, body: str = ""
    ) -> DegradeState:
        """记录响应信息并返回当前降级状态。"""
        domain = _extract_domain(url)
        state = self._states.setdefault(domain, DegradeState())

        state.last_status = status
        state.last_body_len = len(body)

        captcha_signals = ["captcha", "cf-challenge", "recaptcha", "hcaptcha"]
        state.captcha_detected = any(
            s in body.lower() for s in captcha_signals
        )

        if status in (403, 429, 503):
            self._failures[domain] = self._failures.get(domain, 0) + 1
        elif state.captcha_detected:
            self._failures[domain] = self._failures.get(domain, 0) + 1
        elif state.last_body_len < 200:
            self._failures[domain] = self._failures.get(domain, 0) + 1

        # 确定当前层级
        total_failures = self._failures.get(domain, 0)
        http_threshold = self._thresholds.get("api", 3)
        browser_threshold = http_threshold + self._thresholds.get("http", 2)

        if total_failures >= browser_threshold:
            state.layer = "browser"
        elif total_failures >= http_threshold:
            state.layer = "http"
        else:
            state.layer = "api"

        logger.info(
            f"Domain {domain}: layer={state.layer} failures={total_failures} "
            f"status={status} captcha={state.captcha_detected}"
        )
        return state

    def should_degrade(self, ctx: PipelineContext) -> bool:
        """Check if the pipeline should degrade to a fallback engine."""
        if not ctx.target_url:
            return False
        domain = _extract_domain(ctx.target_url)
        state = self._states.get(domain)
        if not state:
            return False
        failures = self._failures.get(domain, 0)
        return failures >= self._thresholds.get("api", 3)

    def degrade(self, current_engine: str) -> str:
        """Degrade engine to next fallback level.

        API → HTTP → Browser chain.
        """
        _DEGRADE_CHAIN = {
            "": "vanilla",
            "api": "vanilla",
            "vanilla": "patched",
            "patched": "camoufox",
            "camoufox": "cloaked",
        }
        return _DEGRADE_CHAIN.get(current_engine, "cloaked")

    def should_use_browser(self, ctx: PipelineContext) -> bool:
        """判断是否需要使用浏览器引擎。

        检查状态码、验证码信号、空响应体以及累计失败次数。
        """
        status = ctx._last_status
        html = ctx.raw_html or ""

        if status in (403, 429, 503):
            return True
        if "captcha" in html.lower() or "cf-challenge" in html.lower():
            return True
        if html and len(html) < 200:
            return True

        domain = _extract_domain(ctx.target_url) if ctx.target_url else ""
        return self._failures.get(domain, 0) >= 3

    def current_layer(self, url: str) -> str:
        """返回当前 URL 对应的降级层级。"""
        if not url:
            return "api"
        domain = _extract_domain(url)
        state = self._states.get(domain)
        return state.layer if state else "api"

    def reset(self, domain: str = "") -> None:
        """重置失败计数器。"""
        if domain:
            self._failures.pop(domain, None)
            self._states.pop(domain, None)
        else:
            self._failures.clear()
            self._states.clear()
