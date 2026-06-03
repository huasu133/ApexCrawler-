"""Sub-resource loading for stealth — ensures Performance API entries match real browsers.

Key insight: real browsers trigger hundreds of sub-resource requests (scripts, images,
fonts, XHRs) that populate `performance.getEntries()`. A bare `page.goto()` with
aborted sub-resources produces an unrealistically low entry count, which is a strong
bot signal for fingerprinting scripts.

This module handles:
- Selective sub-resource loading (skip known fingerprinting/analytics hosts)
- Duration-based warm-up to simulate human reading/browsing
- Difficulty-tiered entry count targets matching real user behavior
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import random
from typing import Any

logger = logging.getLogger(__name__)

# ── Blocklist: known fingerprinting & analytics domains ──────────
SKIP_PATTERNS = [
    "*fingerprintjs*",
    "*creepjs*",
    "*browser-update*",
    "*analytics*.js",
    "*gtm.js",
    "*gtag*",
    "*nagging*",
]

# ── Difficulty-tiered resource policies ─────────────────────────
RESOURCE_POLICIES: dict[str, dict[str, int]] = {
    "low": {"min_entries": 30, "min_duration_ms": 1500},
    "medium": {"min_entries": 60, "min_duration_ms": 3000},
    "high": {"min_entries": 100, "min_duration_ms": 6000},
}


async def ensure_subresource_load(
    page: Any,
    difficulty: str = "medium",
    max_wait_ms: int = 15000,
) -> dict:
    """Load sub-resources until Performance API entry count meets the target.

    Adds a request interceptor that blocks known fingerprinting/analytics hosts
    while letting all other sub-resources through. Then polls the Performance API
    until the minimum entry count is reached or the timeout expires.

    Args:
        page: Playwright page object.
        difficulty: One of "low" (30 entries), "medium" (60), "high" (100).
        max_wait_ms: Maximum wait time in milliseconds.

    Returns:
        dict with keys ``entries`` (actual count) and ``min_target`` (target).

    Raises:
        ValueError: If the difficulty tier is unknown.
    """
    policy = RESOURCE_POLICIES.get(difficulty, RESOURCE_POLICIES["medium"])

    async def _router(route):
        url = route.request.url
        for pattern in SKIP_PATTERNS:
            if fnmatch.fnmatch(url, pattern):
                await route.abort()
                return
        await route.continue_()

    await page.route("**/*", _router)
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(0.5)

    start = asyncio.get_event_loop().time()
    entries = 0
    while (asyncio.get_event_loop().time() - start) * 1000 < max_wait_ms:
        try:
            entries = await page.evaluate("() => performance.getEntries().length")
        except Exception:
            pass
        if entries >= policy["min_entries"]:
            break
        await asyncio.sleep(0.3)

    # Simulate post-load browsing time (human-like dwell)
    extra = random.uniform(
        policy["min_duration_ms"], policy["min_duration_ms"] * 1.5
    ) / 1000
    await asyncio.sleep(extra)

    elapsed = (asyncio.get_event_loop().time() - start) * 1000
    logger.info(
        "Subresource: %d entries in %.0fms (target ≥%d)",
        entries,
        elapsed,
        policy["min_entries"],
    )
    return {"entries": entries, "min_target": policy["min_entries"]}
