"""Human-like behavior simulation: mouse movement, typing, scrolling.

Implements:
- Bézier curve mouse movement with Fitts' law deceleration
- Variable-speed typing with random typos and corrections
- Human-like scrolling with natural pauses
- Warm-up navigation (visit homepage first)
"""

from __future__ import annotations

import asyncio
import logging
import math
import random

logger = logging.getLogger(__name__)


def _bezier_point(p0: tuple, p1: tuple, p2: tuple, p3: tuple, t: float) -> tuple[float, float]:
    """Cubic Bézier interpolation at parameter t in [0, 1]."""
    mt = 1 - t
    x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
    y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
    return x, y


def _fitts_delay(distance: float, target_width: float = 10.0) -> float:
    """Fitts' law: movement time = a + b * log2(D/W + 1).

    Returns delay in seconds with human-like variance.
    """
    a = 0.05   # base reaction time
    b = 0.12   # speed constant
    index_of_difficulty = math.log2(distance / target_width + 1)
    base = a + b * index_of_difficulty
    # Add natural variance (human inconsistency)
    variance = random.gauss(0, base * 0.15)
    return max(0.05, base + variance)


def _generate_bezier_control_points(
    start: tuple[float, float],
    end: tuple[float, float],
) -> tuple[tuple, tuple, tuple, tuple]:
    """Generate natural-looking control points for a mouse movement curve.

    Most human mouse movements curve slightly (not straight lines).
    """
    p0 = start
    p3 = end
    dx = end[0] - start[0]
    dy = end[1] - start[1]

    # Control points: overshoot slightly in one direction, then correct
    overshoot_x = dx * 0.3 + random.uniform(-50, 50)
    overshoot_y = dy * 0.3 + random.uniform(-30, 30)
    p1 = (start[0] + dx * 0.4, start[1] + dy * 0.2 + overshoot_y * 0.5)
    p2 = (end[0] - dx * 0.3, end[1] - dy * 0.1 + overshoot_y * 0.3)

    return p0, p1, p2, p3


class MouseSimulator:
    """Simulates human-like mouse movements using Bézier curves and Fitts' law."""

    def __init__(self, page: object | None = None, session: object | None = None):
        self._page = page
        self._position: tuple[float, float] = (400.0, 300.0)
        self._session = session

    def set_position(self, x: float, y: float) -> None:
        """Set the virtual mouse position (e.g., after a warm-up)."""
        self._position = (x, y)

    async def move_to(
        self,
        target_x: float,
        target_y: float,
        steps: int | None = None,
        emulate: bool = True,
    ) -> None:
        """Move the mouse along a Bézier curve to (target_x, target_y).

        Args:
            target_x, target_y: Target coordinates.
            steps: Number of interpolation steps (auto-calculated from
                   distance if None; longer moves = more steps).
            emulate: If True and page is set, execute actual mouse moves.
        """
        start = self._position
        end = (target_x, target_y)
        p0, p1, p2, p3 = _generate_bezier_control_points(start, end)

        distance = math.hypot(end[0] - start[0], end[1] - start[1])
        if steps is None:
            steps = max(5, min(60, int(distance / random.uniform(8, 20))))
        total_delay = _fitts_delay(distance)

        # Apply session-level mouse speed decay (fatigue)
        if self._session is not None:
            try:
                session_speed = self._session.get_mouse_speed()  # type: ignore
                total_delay *= (1000.0 / max(200.0, session_speed))
            except Exception:
                pass

        step_delay = total_delay / steps

        for i in range(1, steps + 1):
            t = i / steps
            # Ease-in-out: accelerate then decelerate
            t_eased = _ease_in_out_cubic(t)
            x, y = _bezier_point(p0, p1, p2, p3, t_eased)

            if emulate and self._page:
                try:
                    await self._page.mouse.move(int(x), int(y))  # type: ignore
                except Exception:
                    pass

            # Variable inter-step delay (human inconsistency)
            await asyncio.sleep(step_delay * random.uniform(0.7, 1.4))

        self._position = end

    async def click(
        self,
        x: float | None = None,
        y: float | None = None,
        button: str = "left",
        emulate: bool = True,
        element_type: str = "button",
    ) -> None:
        """Move to a position (or stay) and click.

        Hover duration varies by element type, matching real user
        hesitation patterns.

        Args:
            element_type: "link" (fast hesitation), "button" (medium),
                          or "input" (slow — user reads before typing).
        """
        if x is not None and y is not None:
            await self.move_to(x, y, emulate=emulate)

        # Human click delay: hover duration varies by element type
        _HOVER_RANGES = {
            "link": (0.05, 0.15),
            "button": (0.08, 0.20),
            "input": (0.15, 0.30),
        }
        lo, hi = _HOVER_RANGES.get(element_type, (0.08, 0.25))
        await asyncio.sleep(random.uniform(lo, hi))

        if emulate and self._page:
            try:
                await self._page.mouse.click(int(self._position[0]), int(self._position[1]), button=button)  # type: ignore
            except Exception:
                pass


class KeyboardSimulator:
    """Simulates human-like typing with variable speed and typos."""

    _KEY_LAYOUT = {
        # Adjacent keys for typo simulation (QWERTY layout)
        "a": ["q", "w", "s", "z", "x"],
        "b": ["v", "g", "h", "n"],
        "c": ["x", "d", "f", "v"],
        "d": ["s", "e", "r", "f", "c", "x"],
        "e": ["w", "s", "d", "r", "f"],
        "f": ["d", "r", "t", "g", "v", "c"],
        "g": ["f", "t", "y", "h", "b", "v"],
        "h": ["g", "y", "u", "j", "n", "b"],
        "i": ["u", "j", "k", "o"],
        "j": ["h", "u", "i", "k", "m", "n"],
        "k": ["j", "i", "o", "l", "m"],
        "l": ["k", "o", "p"],
        "m": ["n", "j", "k"],
        "n": ["b", "h", "j", "m"],
        "o": ["i", "k", "l", "p"],
        "p": ["o", "l"],
        "q": ["w", "a", "s"],
        "r": ["e", "d", "f", "t"],
        "s": ["a", "w", "e", "d", "x", "z"],
        "t": ["r", "f", "g", "y"],
        "u": ["y", "h", "j", "i"],
        "v": ["c", "f", "g", "b"],
        "w": ["q", "a", "s", "e"],
        "x": ["z", "s", "d", "c"],
        "y": ["t", "g", "h", "u"],
        "z": ["a", "s", "x"],
    }

    def __init__(
        self,
        page: object | None = None,
        typo_rate: float = 0.03,
        wpm: tuple[float, float] = (40, 70),
    ):
        self._page = page
        self._typo_rate = typo_rate
        self._wpm_range = wpm
        self._session_wpm = random.uniform(*self._wpm_range)

    def _char_delay(self) -> float:
        """Per-character delay with session-level WPM stability.

        Session WPM stays stable (±5% jitter) rather than random
        per-character, matching real user consistency.
        """
        wpm = self._session_wpm * random.uniform(0.95, 1.05)
        chars_per_minute = wpm * 5  # standard: 5 chars per word
        return 60.0 / chars_per_minute

    def _random_neighbor(self, char: str) -> str:
        """Get a random adjacent key for typo simulation."""
        lower = char.lower()
        neighbors = self._KEY_LAYOUT.get(lower, [])
        if not neighbors:
            return char
        return random.choice(neighbors)

    async def type(self, text: str, emulate: bool = True) -> None:
        """Type text with variable delays and occasional typos.

        Args:
            text: The string to type.
            emulate: If True and page is set, use actual keyboard input.
        """
        for i, char in enumerate(text):
            # Typo simulation — multiple correction modes
            if char.isalpha() and random.random() < self._typo_rate:
                wrong_char = self._random_neighbor(char)
                if wrong_char != char:
                    mode = random.choices(
                        ["backspace_once", "backspace_multi", "backspace_word", "ignore"],
                        weights=[0.40, 0.15, 0.25, 0.20],
                        k=1,
                    )[0]

                    # Type the wrong character(s)
                    if mode != "ignore":
                        chars_to_type = wrong_char
                        if mode == "backspace_multi":
                            chars_to_type += self._random_neighbor(wrong_char)
                        elif mode == "backspace_word":
                            chars_to_type += wrong_char + wrong_char
                        if emulate and self._page:
                            try:
                                await self._page.keyboard.type(chars_to_type)  # type: ignore
                            except Exception:
                                pass
                        await asyncio.sleep(self._char_delay() * random.uniform(0.3, 0.7))

                        # Correct with backspaces
                        backspace_count = len(chars_to_type)
                        for _ in range(backspace_count):
                            if emulate and self._page:
                                try:
                                    await self._page.keyboard.press("Backspace")  # type: ignore
                                except Exception:
                                    pass
                            await asyncio.sleep(self._char_delay() * 0.3)
                    else:
                        # "ignore" mode: skip the typo, just pause briefly
                        await asyncio.sleep(self._char_delay() * random.uniform(0.2, 0.5))

            # Type the actual character
            if emulate and self._page:
                try:
                    await self._page.keyboard.type(char)  # type: ignore
                except Exception:
                    pass

            # Variable delay between keystrokes
            delay = self._char_delay() * random.uniform(0.6, 1.5)
            # Longer pauses at word boundaries (space)
            if char == " " and random.random() < 0.2:
                delay *= random.uniform(1.5, 3.0)
            await asyncio.sleep(delay)


class ScrollSimulator:
    """Simulates human-like scrolling with variable speed and natural pauses."""

    def __init__(self, page: object | None = None):
        self._page = page

    async def scroll(
        self,
        direction: str = "down",
        distance: int | None = None,
        duration: float | None = None,
        emulate: bool = True,
    ) -> None:
        """Scroll with bursty wheel events mimicking human scanning.

        Instead of uniform ease-in-out, this produces 2–5 frame bursts
        of rapid wheel events separated by random pauses (reader
        scanning, attention shifts).

        Args:
            direction: "up" or "down".
            distance: Scroll distance in pixels. Random if None.
            duration: Total scroll duration in seconds. Proportional to distance if None.
            emulate: If True and page is set, execute actual scroll events.
        """
        if distance is None:
            distance = random.randint(300, 1200)
        if duration is None:
            duration = distance / random.uniform(400, 800)

        sign = -1 if direction == "down" else 1
        total_pixels = sign * distance
        remaining = total_pixels

        while abs(remaining) > 0:
            # Burst: 2–5 fast frames in quick succession
            burst_frames = random.randint(2, 5)
            burst_pixels = min(abs(remaining), random.randint(80, 250))
            direction_sign = 1 if remaining > 0 else -1
            per_frame = direction_sign * burst_pixels / burst_frames

            for _ in range(burst_frames):
                delta = int(per_frame)
                if delta == 0:
                    continue
                if emulate and self._page:
                    try:
                        await self._page.mouse.wheel(0, delta)  # type: ignore
                    except Exception:
                        pass
                await asyncio.sleep(random.uniform(0.015, 0.04))

            remaining -= direction_sign * burst_pixels

            # Random pause between bursts (reader scanning)
            if abs(remaining) > 10:
                await asyncio.sleep(random.uniform(0.2, 1.5))

    async def scroll_to_bottom(
        self,
        emulate: bool = True,
        max_scrolls: int = 10,
    ) -> None:
        """Scroll slowly to the bottom of the page."""
        for _ in range(max_scrolls):
            await self.scroll(direction="down", distance=random.randint(400, 900), emulate=emulate)
            # Pause as if reading
            await asyncio.sleep(random.uniform(0.5, 2.0))

    async def scroll_to_element(
        self,
        selector: str,
        emulate: bool = True,
    ) -> None:
        """Scroll until a specific element is visible.

        In a real implementation, this would use ScrollIntoView.
        Here we simulate with progressive scrolls.
        """
        for _ in range(random.randint(3, 6)):
            await self.scroll(direction="down", distance=random.randint(200, 500), emulate=emulate)
            await asyncio.sleep(random.uniform(0.3, 0.8))


class Humanizer:
    """Orchestrator for human-like behavior simulation.

    Combines mouse, keyboard, and scroll simulation with warm-up
    navigation patterns.
    """

    def __init__(
        self,
        page: object | None = None,
        typo_rate: float = 0.03,
        wpm: tuple[float, float] = (45, 65),
        session: object | None = None,
        viewport_w: float = 1920,
        viewport_h: float = 1080,
    ):
        self.page = page
        self.mouse = MouseSimulator(page)
        self.keyboard = KeyboardSimulator(page, typo_rate=typo_rate, wpm=wpm)
        self.scroll = ScrollSimulator(page)

        # Passive signal profiler — injects scroll-depth, mouse-zone,
        # tab-switch, sendBeacon interceptor, and DNS prefetch signals
        from .passive_signals import PassiveSignalProfiler

        self._passive = PassiveSignalProfiler(
            page, viewport_w=viewport_w, viewport_h=viewport_h
        )
        self._session = session or self._passive.session  # SessionBehavior for cross-page consistency

        # Wire session into mouse simulator for speed decay
        if self._session is not None:
            self.mouse._session = self._session

    async def warm_up(
        self,
        homepage: str,
        viewport_width: int = 1920,
        viewport_height: int = 1080,
        emulate: bool = True,
    ) -> None:
        """Warm-up navigation: visit homepage first, scroll a bit, then proceed."""

        if emulate and self.page:
            try:
                await self.page.goto(homepage, wait_until="domcontentloaded")
            except Exception:
                pass

        await asyncio.sleep(random.uniform(1.0, 2.5))

        self.mouse.set_position(
            viewport_width * random.uniform(0.3, 0.7),
            viewport_height * random.uniform(0.3, 0.6),
        )
        await self.mouse.move_to(
            viewport_width * random.uniform(0.4, 0.6),
            viewport_height * 0.4,
            steps=20,
            emulate=emulate,
        )

        await self.scroll.scroll("down", distance=random.randint(200, 500), emulate=emulate)
        await asyncio.sleep(random.uniform(1.5, 4.0))

        # Passive signals: DNS prefetch noise + sendBeacon interceptor
        if emulate and self.page:
            try:
                await self._passive.inject_dns_prefetch(count=3)
            except Exception:
                logger.debug("DNS prefetch injection skipped")
            try:
                await self._passive.monitor_sendbeacon()
            except Exception:
                logger.debug("sendBeacon monitor skipped")

        # Increment session page count for cross-page behavior
        if self._session:
            self._session.increment_page()

    async def idle(
        self,
        duration_s: float = 2.0,
        viewport_w: int = 1920,
        viewport_h: int = 1080,
        emulate: bool = True,
    ) -> None:
        """Simulate mouse idle micro-jitter during reading pauses.

        Real users don't hold the mouse perfectly still — they make
        tiny involuntary movements (<5px) every few hundred ms.

        Args:
            duration_s: How long to simulate idle (seconds).
            viewport_w, viewport_h: Current viewport dimensions.
            emulate: If True, execute real mouse moves via Playwright.
        """
        from .passive_signals import MouseIdlePattern

        idle_pattern = MouseIdlePattern()

        # Use profiler's sampled mouse zone as idle base position
        zone_x, zone_y = self._passive.mouse_zone
        base_x = zone_x if emulate else viewport_w * random.uniform(0.3, 0.5)
        base_y = zone_y if emulate else viewport_h * random.uniform(0.3, 0.5)

        if emulate and self.page:
            await idle_pattern.run_idle(
                self.page, duration_s=duration_s, base_x=base_x, base_y=base_y
            )
        else:
            _ = idle_pattern.generate_idle_moves(
                duration_s=duration_s,
            )

    async def pause(self, min_seconds: float = 0.5, max_seconds: float = 2.0) -> None:
        """Random pause between actions to simulate thinking time."""
        await asyncio.sleep(random.uniform(min_seconds, max_seconds))

    async def human_scroll(
        self,
        direction: str = "down",
        emulate: bool = True,
        page_height: int = 5000,
    ) -> None:
        """Scroll with distance sampled from the passive profiler's scroll-depth
        distribution.  Uses the power-law distribution calibrated against
        real-world browsing data (Nielsen Norman Group).

        Args:
            direction: "up" or "down".
            emulate: If True, execute actual scroll events via Playwright.
            page_height: Estimated page height in pixels (used with scroll depth
                         fraction to compute pixel distance).
        """
        depth_fraction = self._passive.scroll_depth
        distance = int(page_height * depth_fraction)
        distance = max(200, min(distance, 4000))

        await self.scroll.scroll(
            direction=direction, distance=distance, emulate=emulate
        )
        self._passive.resample()  # re-sample for next page interaction

        logger.debug(
            "human_scroll: depth=%.2f distance=%d direction=%s",
            depth_fraction, distance, direction,
        )

    async def human_wait(self, base: float = 1.0) -> None:
        """Wait a variable amount of time (normal distribution around base)."""
        delay = abs(random.gauss(base, base * 0.3))
        await asyncio.sleep(delay)


def _ease_in_out_cubic(t: float) -> float:
    """Smooth ease-in-out: accelerates, then decelerates."""
    if t < 0.5:
        return 4 * t**3
    return 1 - (-2 * t + 2) ** 3 / 2
