"""Content-driven timing controller — real browsing patterns over time.

Real human browsing is heavily time-of-day dependent. This module models:

- **Safe windows**: crawl during peak human activity, avoid dead-of-night.
- **Speed variation**: faster during work hours, slower in the evening.
- **Content dwell**: time-on-page follows a lognormal distribution driven by
  page size (KB), text length, and image count.
- **Session gaps**: intervals between successive pages grow throughout a session
  (tab fatigue).
- **CrawlWindow**: structured time slots for scheduling.
"""

from __future__ import annotations

import calendar
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Sequence


# ── Time-of-day helpers ───────────────────────────────────────

_DANGER_START = time(0, 0)   # midnight
_DANGER_END = time(6, 0)     # 6 AM


def is_safe_time(now: datetime | None = None) -> bool:
    """Return False during the high-risk window 00:00-05:59.

    Most legitimate users are asleep during these hours.  Crawling at
    3 AM is a strong signal for automated traffic.
    """
    now = now or datetime.now()
    t = now.time()
    if _DANGER_START <= t < _DANGER_END:
        return False
    return True


# ── Time multiplier ───────────────────────────────────────────

# Hour → speed multiplier.  <1.0 = slower (more human-like delays),
# >1.0 = faster (work hours).  Values are rough approximations of
# real browsing cadence.
_HOURLY_MULTIPLIER: dict[int, float] = {
    0: 0.3, 1: 0.3, 2: 0.2, 3: 0.2, 4: 0.25, 5: 0.3,
    6: 0.6, 7: 0.8,
    8: 1.0, 9: 1.0, 10: 1.0, 11: 1.0,
    12: 0.9, 13: 0.85, 14: 1.0, 15: 1.0, 16: 1.0, 17: 0.9,
    18: 0.7, 19: 0.6, 20: 0.5, 21: 0.5, 22: 0.4, 23: 0.35,
}


def get_time_multiplier(now: datetime | None = None) -> float:
    """Return a speed multiplier for the current hour.

    Work hours (8-17) → 1.0x (natural pace).
    Early morning / late evening → slower, higher delays.
    Night (0-6) → heavily slowed.
    """
    now = now or datetime.now()
    return _HOURLY_MULTIPLIER.get(now.hour, 0.7)


# ── Content dwell time ────────────────────────────────────────


def content_dwell_time(
    page_size_kb: float = 100.0,
    text_length: int = 2000,
    image_count: int = 5,
    page_type: str = "article",
) -> float:
    """Estimate human dwell time using a lognormal distribution.

    The median dwell time scales with page complexity:

    - **page_size_kb**: total transferred bytes.
    - **text_length**: approximate character count.
    - **image_count**: number of ``<img>`` tags.
    - **page_type**: "category" (listing, sigma=0.25),
      "product" (detail, sigma=0.4),
      "article" (long-form, sigma=0.5).

    Returns dwell time in seconds.

    Background
    ----------
    Research on web reading behaviour shows time-on-page follows a
    lognormal distribution with median ~10-30s for text-heavy pages
    and ~5-15s for image-heavy pages.  This function maps content
    metrics to a plausible median, then samples from the distribution.
    """
    # Base median: text-dominated read time (~150 wpm → ~5 chars/s)
    reading_time = text_length / 5.0

    # Visual scan time: ~0.5-1.0s per image
    image_time = image_count * 0.7

    # Page load overhead proportion (larger pages take longer to digest)
    size_factor = math.log1p(page_size_kb) / 5.0

    # Composite median dwell (seconds)
    median = reading_time * 0.3 + image_time * 0.4 + size_factor * 0.3

    # Clip to reasonable bounds (2s minimum, 5min maximum)
    median = max(2.0, min(300.0, median))

    # Lognormal parameters: sigma varies by page type
    # - Category listing: tight distribution (quick scanning)
    # - Product detail: moderate variance
    # - Long article: wide distribution (read speeds vary)
    _SIGMA_BY_TYPE = {
        "category": 0.25,
        "product": 0.40,
        "article": 0.50,
    }
    mu = math.log(median)
    sigma = _SIGMA_BY_TYPE.get(page_type, 0.50)
    return random.lognormvariate(mu, sigma)


# ── Session gap ───────────────────────────────────────────────


def session_gap(page_count: int) -> float:
    """Return the inter-page interval for a browsing session.

    Real users: the first few pages are loaded quickly (1-3s gap),
    but as the session progresses intervals grow due to tab fatigue,
    context switching, and deeper reading.

    Args:
        page_count: Number of pages already visited in this session
                    (0-based — 0 means *before* the first page load).

    Returns gap in seconds.
    """
    # Power-law growth: base ~1s + 0.4 * page_count^1.2, plus noise
    base = 1.0
    fatigue = 0.4 * (page_count ** 1.2)
    noise = random.gauss(0, 0.3 * (page_count + 1))
    return max(1.0, base + fatigue + noise)


# ── Crawl window dataclass ────────────────────────────────────


@dataclass
class CrawlWindow:
    """Structured time slots for scheduling crawl sessions.

    Each field is a list of ``(start_hour, end_hour)`` tuples in local time
    (0-24, floats allowed e.g. 8.5 = 08:30).

    Example::

        window = CrawlWindow(
            work_morning=[(8, 12)],
            evening=[(18, 22)],
            avoid=[(0, 6)],
        )
    """

    work_morning: list[tuple[float, float]] = field(default_factory=lambda: [(8, 12)])
    work_afternoon: list[tuple[float, float]] = field(default_factory=lambda: [(13, 17)])
    evening: list[tuple[float, float]] = field(default_factory=lambda: [(18, 22)])
    avoid: list[tuple[float, float]] = field(default_factory=lambda: [(0, 6)])

    def is_active(self, now: datetime | None = None) -> bool:
        """Return True if the current time falls in an active window.

        Active = in work_morning OR work_afternoon OR evening,
        and NOT in avoid.
        """
        now = now or datetime.now()
        hour = now.hour + now.minute / 60.0

        # Check avoid windows first
        for start, end in self.avoid:
            if start <= hour < end:
                return False

        # Check active windows
        for window in (*self.work_morning, *self.work_afternoon, *self.evening):
            if window[0] <= hour < window[1]:
                return True
        return False

    def active_windows_today(self, now: datetime | None = None) -> int:
        """Count how many active windows remain today.

        Used by schedulers to decide whether to start a new session
        or wait until tomorrow.
        """
        now = now or datetime.now()
        hour = now.hour + now.minute / 60.0
        remaining = 0
        for window in (*self.work_morning, *self.work_afternoon, *self.evening):
            if window[1] > hour:
                remaining += 1
        return remaining

    def next_start_time(self, now: datetime | None = None) -> datetime | None:
        """Return the next datetime when an active window begins.

        Returns None if no more active windows today — caller should
        schedule for tomorrow.
        """
        now = now or datetime.now()
        hour = now.hour + now.minute / 60.0

        candidate: float | None = None
        for window in (*self.work_morning, *self.work_afternoon, *self.evening):
            if window[0] > hour:
                if candidate is None or window[0] < candidate:
                    candidate = window[0]

        if candidate is None:
            return None

        h = int(candidate)
        m = int((candidate - h) * 60)
        return now.replace(hour=h, minute=m, second=0, microsecond=0)


# ── Utility: weekday-aware window builder ─────────────────────


def weekday_window(hour: int) -> float:
    """Helper: return hour as float (e.g. 8 → 8.0, 8:30 → 8.5)."""
    return float(hour)


_MONTH_WEEKDAYS = [calendar.MONDAY, calendar.TUESDAY, calendar.WEDNESDAY,
                   calendar.THURSDAY, calendar.FRIDAY]


def is_weekday(now: datetime | None = None) -> bool:
    """Return True if today is a weekday (Mon-Fri)."""
    now = now or datetime.now()
    return now.date().weekday() in (0, 1, 2, 3, 4)


# ── TimingScheduler ───────────────────────────────────────────


class TimingScheduler:
    """Content-driven timing scheduler that models human browsing cadence.

    Combines time-of-day multiplier, content dwell estimation, and
    session-fatigue gaps into a single delay value.  Each call to
    :meth:`compute_delay` advances an internal page counter so gaps
    grow naturally over a session.

    Usage::

        scheduler = TimingScheduler()
        delay = scheduler.compute_delay(page_size_kb=120, text_length=3000)
        await asyncio.sleep(delay)
    """

    def __init__(self, crawl_window: CrawlWindow | None = None) -> None:
        self._window = crawl_window or CrawlWindow()
        self._page_count = 0

    def compute_delay(
        self,
        page_size_kb: float = 50.0,
        text_length: int = 1000,
        image_count: int = 3,
    ) -> float:
        """Return a human-like inter-request delay in seconds.

        The delay is composed of:
        1. Content dwell time (lognormal, driven by page metrics)
        2. Session gap (grows with ``_page_count``)
        3. Time-of-day multiplier (faster during work hours)

        Returns at least 1.0 second.
        """
        multiplier = get_time_multiplier()
        dwell = content_dwell_time(page_size_kb, text_length, image_count)
        gap = session_gap(self._page_count)
        self._page_count += 1

        raw_delay = dwell + gap
        if multiplier > 0:
            raw_delay = raw_delay / multiplier

        return max(1.0, raw_delay)

    def reset(self) -> None:
        """Reset the internal page counter (new session)."""
        self._page_count = 0
