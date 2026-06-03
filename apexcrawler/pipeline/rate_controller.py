"""6 级自适应速率控制，基于响应信号密度自动升降。

L0: 5 req/s  (正常)
L1: 2 req/s  (429首次)
L2: 1 req/s  (连续429)
L3: 0.5 req/s (响应异常)
L4: 0.1 req/s (截断响应)
L5: 0.05 req/s (验证码)

无信号时逐步恢复。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

logger = logging.getLogger(__name__)


class RateController:
    """6 级响应速率控制器。

    基于响应信号(状态码/验证码/空响应)自动升级速率等级，
    无信号时逐步恢复至正常速率。
    """

    LEVELS = [5, 2, 1, 0.5, 0.1, 0.05]

    def __init__(self):
        self._level = 0
        self._last_request = 0.0
        self._recovery_counter = 0

    @property
    def current_rate(self) -> float:
        """当前允许的请求速率 (req/s)。"""
        return self.LEVELS[self._level]

    @property
    def current_level(self) -> int:
        """当前速率等级索引 (0-5)。"""
        return self._level

    @property
    def level_name(self) -> str:
        """当前等级的语义化名称。"""
        names = {
            0: "normal",
            1: "caution",
            2: "throttled",
            3: "degraded",
            4: "minimal",
            5: "captcha_cooldown",
        }
        return names.get(self._level, "unknown")

    async def throttle(self):
        """根据当前速率等待适当时间。"""
        interval = 1.0 / self.current_rate if self.current_rate > 0 else 20
        elapsed = time.monotonic() - self._last_request
        if elapsed < interval:
            wait_time = interval - elapsed
            await asyncio.sleep(wait_time)
        self._last_request = time.monotonic()
        self._maybe_recover()

    def signal(self, status: int = 0, html: str = ""):
        """根据响应信号调整速率等级。

        Args:
            status: HTTP 状态码
            html: 响应体内容
        """
        previous_level = self._level

        if status == 429:
            self._upgrade()
        elif status in (403, 503):
            self._level = min(self._level + 2, len(self.LEVELS) - 1)
        elif html and "captcha" in html.lower():
            self._level = 5
        elif html and len(html) < 100:
            self._upgrade()

        if self._level != previous_level:
            logger.warning(
                f"Rate level changed: {previous_level}→{self._level} "
                f"({self.LEVELS[self._level]} req/s) "
                f"reason: status={status} body_len={len(html)}"
            )

        self._recovery_counter = 0

    def _upgrade(self):
        """提升一级速率限制。"""
        self._level = min(self._level + 1, len(self.LEVELS) - 1)

    def _maybe_recover(self):
        """无信号时逐步恢复速率。"""
        self._recovery_counter += 1
        if self._recovery_counter > 50 and self._level > 0:
            self._level -= 1
            self._recovery_counter = 0
            logger.info(
                f"Rate recovered to level {self._level} "
                f"({self.LEVELS[self._level]} req/s)"
            )

    def reset(self):
        """重置到正常速率等级。"""
        self._level = 0
        self._last_request = 0.0
        self._recovery_counter = 0
        logger.info("Rate controller reset to level 0 (normal)")
