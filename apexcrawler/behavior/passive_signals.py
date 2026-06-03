"""Passive behavioral signals — realistic user patterns that anti-bot
scripts observe passively.

Active behaviors (mouse movement, typing, scrolling) are covered by
``Humanizer``.  This module handles signals that are *detected* rather
than *performed*:

- Scroll depth distribution (power-law: most users don't reach the bottom).
- Mouse heat-map zone sampling (where users position their cursors).
- Micro-jitter during reading pauses (tiny involuntary movements).
- Visibility-change events (tab switching).
- ``sendBeacon`` call interception (prevent silent analytics pings).
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
#  Scroll depth — power-law distribution
# ════════════════════════════════════════════════════════════════

# Real-world scroll depth percentages (Nielsen Norman Group, Chartbeat).
# Only ~10% of users reach the bottom of a page.
_SCROLL_DEPTH_BUCKETS: list[tuple[float, str]] = [
    (0.38, "top_25"),       # 38% stop within the top 25% of the page
    (0.25, "upper_mid"),    # 25% stop at 25-50%
    (0.17, "lower_mid"),    # 17% stop at 50-75%
    (0.10, "bottom_10"),    # 10% stop at 75-90% (near bottom)
    (0.10, "bottom"),       # 10% reach the very bottom
]


def sample_scroll_depth() -> float:
    """Sample scroll depth fraction (0.0–1.0) from a power-law distribution.

    Returns a value between 0 and 1 representing how far down the page
    the simulated user scrolls.  Distribution is calibrated to match
    real-world browsing data:

    - 38% of sessions stop in the top 25% of the page.
    - Only ~20% reach beyond 75%.
    - ~10% reach the very bottom.

    Returns:
        float in [0.01, 1.0] representing scroll depth fraction.
    """
    # Inverse-transform sampling from our empirical CDF
    r = random.random()
    cumulative = 0.0
    for prob, bucket in _SCROLL_DEPTH_BUCKETS:
        cumulative += prob
        if r <= cumulative:
            # Map bucket to a uniform sub-range
            idx = [b[1] for b in _SCROLL_DEPTH_BUCKETS].index(bucket)
            start_frac = idx / len(_SCROLL_DEPTH_BUCKETS)
            end_frac = (idx + 1) / len(_SCROLL_DEPTH_BUCKETS)
            return random.uniform(start_frac, end_frac)
    return 1.0


# ════════════════════════════════════════════════════════════════
#  Mouse zone sampling — heat-map distribution
# ════════════════════════════════════════════════════════════════


def sample_mouse_zone(
    viewport_w: float = 1920,
    viewport_h: float = 1080,
) -> tuple[float, float]:
    """Sample a (x, y) coordinate based on real mouse heat-map zones.

    Distribution across zones:
    - Navigation area (top 15%): 30% — users often hover menus/tabs.
    - Content area (middle 60%): 40% — the main reading zone.
    - Sidebar area (right 25%): 20% — scrollbar, widgets.
    - Footer area (bottom 15%): 10% — rarely hovered.

    Args:
        viewport_w: Viewport width in pixels.
        viewport_h: Viewport height in pixels.

    Returns:
        (x, y) tuple in pixel coordinates.
    """
    zone = random.choices(
        ["nav", "content", "sidebar", "footer"],
        weights=[0.30, 0.40, 0.20, 0.10],
        k=1,
    )[0]

    if zone == "nav":
        # Top strip — menu bars, breadcrumbs, toolbars
        x = random.uniform(100, viewport_w * 0.85)
        y = random.uniform(20, viewport_h * 0.15)
    elif zone == "content":
        # Middle area — article body, product listings
        x = random.uniform(100, viewport_w * 0.75)
        y = random.uniform(viewport_h * 0.18, viewport_h * 0.78)
    elif zone == "sidebar":
        # Right side — scrollbar, side widgets, ads
        x = random.uniform(viewport_w * 0.75, viewport_w - 20)
        y = random.uniform(50, viewport_h * 0.85)
    else:
        # Footer — bottom area
        x = random.uniform(100, viewport_w * 0.80)
        y = random.uniform(viewport_h * 0.82, viewport_h - 30)

    # Add micro-offset to avoid exact pixel alignment (bot signal)
    x += random.uniform(-1.5, 1.5)
    y += random.uniform(-1.5, 1.5)

    return (x, y)


# ════════════════════════════════════════════════════════════════
#  Mouse idle pattern — micro-jitter during reading pauses
# ════════════════════════════════════════════════════════════════


class MouseIdlePattern:
    """Generates micro-jitter movements during reading pauses.

    Real users' mice never stay perfectly still — there are tiny
    involuntary movements (1–3 px) even when "idle".  This class
    produces a sequence of such jitter events.

    Usage::

        pattern = MouseIdlePattern()
        for dx, dy, delay in pattern.generate_idle_moves(duration_s=3.0):
            await page.mouse.move(current_x + dx, current_y + dy)
            await asyncio.sleep(delay)
    """

    def __init__(
        self,
        jitter_radius: float = 3.0,
        min_interval_ms: float = 200,
        max_interval_ms: float = 800,
    ) -> None:
        self._jitter_radius = jitter_radius
        self._min_interval = min_interval_ms / 1000.0
        self._max_interval = max_interval_ms / 1000.0

    def generate_idle_moves(
        self,
        duration_s: float = 2.0,
    ) -> list[tuple[float, float, float]]:
        """Generate a sequence of (dx, dy, delay) micro-jitter moves.

        Args:
            duration_s: Total idle period in seconds.

        Returns:
            List of (dx, dy, inter_move_delay_s) tuples.  Each entry
            represents a tiny mouse movement followed by a pause.
        """
        elapsed = 0.0
        moves: list[tuple[float, float, float]] = []

        while elapsed < duration_s:
            # Tiny displacement in a random direction
            angle = random.uniform(0, 2 * math.pi)
            magnitude = random.random() * self._jitter_radius
            dx = math.cos(angle) * magnitude
            dy = math.sin(angle) * magnitude

            # Jitter interval varies
            delay = random.uniform(self._min_interval, self._max_interval)
            elapsed += delay
            moves.append((dx, dy, delay))

        return moves

    async def run_idle(
        self,
        page: Any,
        duration_s: float = 2.0,
    ) -> None:
        """Execute the idle pattern on a Playwright page.

        Moves the mouse in micro-jitter patterns, then returns to
        approximately the starting position.
        """
        moves = self.generate_idle_moves(duration_s)
        for dx, dy, delay in moves:
            try:
                await page.mouse.move(
                    round(dx),
                    round(dy),
                    steps=1,
                )
            except Exception:
                pass
            await asyncio.sleep(delay)

        # Return to origin with a small counter-move
        total_dx = sum(m[0] for m in moves)
        total_dy = sum(m[1] for m in moves)
        try:
            await page.mouse.move(
                round(-total_dx * 0.7),
                round(-total_dy * 0.7),
                steps=2,
            )
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════
#  Tab switch simulation — visibilitychange event
# ════════════════════════════════════════════════════════════════


async def simulate_tab_switch(
    page: Any,
    switch_duration_s: float = 5.0,
) -> None:
    """Simulate the user switching away from and back to the tab.

    Many anti-bot scripts monitor the ``visibilitychange`` event and
    the ``document.hidden`` property.  A page that is *always* visible
    is suspicious — real users frequently switch tabs.

    This function:
    1. Fires a ``visibilitychange`` event with ``document.hidden = true``.
    2. Waits for the specified duration.
    3. Fires another ``visibilitychange`` event with ``document.hidden = false``.

    Args:
        page: Playwright page object.
        switch_duration_s: How long the tab is "away" (seconds).
    """
    logger.info(
        "Tab switch: away for %.1fs (visibilitychange → hidden → visible)",
        switch_duration_s,
    )

    # Fire hidden event
    try:
        await page.evaluate(
            """() => {
                Object.defineProperty(document, 'hidden', {value: true, configurable: true});
                Object.defineProperty(document, 'visibilityState', {value: 'hidden', configurable: true});
                document.dispatchEvent(new Event('visibilitychange'));
            }"""
        )
    except Exception:
        logger.warning("Tab switch: failed to dispatch hidden event")

    await asyncio.sleep(switch_duration_s)

    # Fire visible event
    try:
        await page.evaluate(
            """() => {
                Object.defineProperty(document, 'hidden', {value: false, configurable: true});
                Object.defineProperty(document, 'visibilityState', {value: 'visible', configurable: true});
                document.dispatchEvent(new Event('visibilitychange'));
            }"""
        )
    except Exception:
        logger.warning("Tab switch: failed to dispatch visible event")

    # Short pause after returning (user re-orients)
    await asyncio.sleep(random.uniform(0.5, 1.5))


# ════════════════════════════════════════════════════════════════
#  sendBeacon monitoring — intercept analytics pings
# ════════════════════════════════════════════════════════════════


async def monitor_sendbeacon(page: Any) -> dict[str, int]:
    """Intercept and log ``navigator.sendBeacon`` calls.

    Some anti-bot scripts use ``sendBeacon`` to silently report
    detected automation back to their servers.  By intercepting these
    calls we can:

    - Log what data is being sent.
    - Prevent the beacon from firing (keeping our fingerprint hidden).
    - Count how many beacons were intercepted.

    Args:
        page: Playwright page object.

    Returns:
        dict with ``intercepted`` (count of blocked beacons) and
        ``urls`` (list of target URLs that were blocked).
    """
    beacons_captured: list[str] = []

    try:
        result = await page.evaluate(
            """() => {
                window.__apex_beacons = [];
                const orig = navigator.sendBeacon.bind(navigator);
                navigator.sendBeacon = function(url, data) {
                    window.__apex_beacons.push({url: url, size: data ? data.byteLength || data.length || 0 : 0});
                    return true;  // pretend success to avoid retries
                };
                return {installed: true};
            }"""
        )
    except Exception:
        logger.warning("monitor_sendbeacon: failed to install interceptor")
        return {"intercepted": 0, "urls": []}

    # Read back captured beacons
    try:
        beacons = await page.evaluate("() => window.__apex_beacons || []")
        beacons_captured = [b["url"] for b in beacons]
    except Exception:
        pass

    if beacons_captured:
        logger.info(
            "sendBeacon: intercepted %d calls to %d unique URLs",
            len(beacons_captured),
            len(set(beacons_captured)),
        )
        for url in beacons_captured:
            logger.debug("sendBeacon → %s", url)

    return {
        "intercepted": len(beacons_captured),
        "urls": list(set(beacons_captured)),
    }
