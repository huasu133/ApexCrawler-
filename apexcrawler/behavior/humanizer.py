"""Human-like behavior simulation: mouse movement, typing, scrolling.

Implements:
- Bézier curve mouse movement with Fitts' law deceleration
- Variable-speed typing with random typos and corrections
- Human-like scrolling with natural pauses
- Warm-up navigation (visit homepage first)
"""

from __future__ import annotations

import asyncio
import math
import random
import time


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

    def __init__(self, page: object | None = None):
        self._page = page
        self._position: tuple[float, float] = (400.0, 300.0)

    def set_position(self, x: float, y: float) -> None:
        """Set the virtual mouse position (e.g., after a warm-up)."""
        self._position = (x, y)

    async def move_to(
        self,
        target_x: float,
        target_y: float,
        steps: int = 30,
        emulate: bool = False,
    ) -> None:
        """Move the mouse along a Bézier curve to (target_x, target_y).

        Args:
            target_x, target_y: Target coordinates.
            steps: Number of interpolation steps (more = smoother).
            emulate: If True and page is set, execute actual mouse moves.
        """
        start = self._position
        end = (target_x, target_y)
        p0, p1, p2, p3 = _generate_bezier_control_points(start, end)

        distance = math.hypot(end[0] - start[0], end[1] - start[1])
        total_delay = _fitts_delay(distance)
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
        emulate: bool = False,
    ) -> None:
        """Move to a position (or stay) and click."""
        if x is not None and y is not None:
            await self.move_to(x, y, emulate=emulate)

        # Human click delay: brief hover before click
        await asyncio.sleep(random.uniform(0.08, 0.25))

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

    def _char_delay(self) -> float:
        """Random per-character delay based on WPM.

        Average WPM: 40–70 → ~171–300 ms per character.
        """
        wpm = random.uniform(*self._wpm_range)
        chars_per_minute = wpm * 5  # standard: 5 chars per word
        return 60.0 / chars_per_minute

    def _random_neighbor(self, char: str) -> str:
        """Get a random adjacent key for typo simulation."""
        lower = char.lower()
        neighbors = self._KEY_LAYOUT.get(lower, [])
        if not neighbors:
            return char
        return random.choice(neighbors)

    async def type(self, text: str, emulate: bool = False) -> None:
        """Type text with variable delays and occasional typos.

        Args:
            text: The string to type.
            emulate: If True and page is set, use actual keyboard input.
        """
        for i, char in enumerate(text):
            # Typo simulation: type wrong adjacent key, then backspace
            if char.isalpha() and random.random() < self._typo_rate:
                wrong_char = self._random_neighbor(char)
                if wrong_char != char:
                    if emulate and self._page:
                        try:
                            await self._page.keyboard.type(wrong_char)  # type: ignore
                        except Exception:
                            pass
                    await asyncio.sleep(self._char_delay() * random.uniform(0.3, 0.7))
                    # Correct the typo
                    if emulate and self._page:
                        try:
                            await self._page.keyboard.press("Backspace")  # type: ignore
                        except Exception:
                            pass
                    await asyncio.sleep(self._char_delay() * 0.5)

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
        emulate: bool = False,
    ) -> None:
        """Scroll with human-like acceleration/deceleration pattern.

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

        # Break scroll into micro-steps with pause probability
        steps = random.randint(8, 20)
        step_size = total_pixels / steps

        for i in range(steps):
            t = i / steps
            # Ease-in-out for smooth motion
            eased = _ease_in_out_cubic(t)
            delta = int(step_size * (eased + 0.3))  # ensure non-zero

            if emulate and self._page:
                try:
                    await self._page.mouse.wheel(0, delta)  # type: ignore
                except Exception:
                    pass

            step_delay = duration / steps * random.uniform(0.8, 1.3)
            await asyncio.sleep(step_delay)

            # Occasional pause (reader scanning content)
            if random.random() < 0.15:
                await asyncio.sleep(random.uniform(0.3, 1.5))

    async def scroll_to_bottom(
        self,
        emulate: bool = False,
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
        emulate: bool = False,
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
    ):
        self.page = page
        self.mouse = MouseSimulator(page)
        self.keyboard = KeyboardSimulator(page, typo_rate=typo_rate, wpm=wpm)
        self.scroll = ScrollSimulator(page)
        self._session = session  # SessionBehavior for cross-page consistency

    async def warm_up(
        self,
        homepage: str,
        viewport_width: int = 1920,
        viewport_height: int = 1080,
        emulate: bool = False,
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

        # Increment session page count for cross-page behavior
        if self._session:
            self._session.increment_page()

    async def idle(
        self,
        duration_s: float = 2.0,
        viewport_w: int = 1920,
        viewport_h: int = 1080,
        emulate: bool = False,
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
        moves = idle_pattern.generate_idle_moves(
            duration_ms=int(duration_s * 1000),
            base_x=viewport_w * random.uniform(0.3, 0.5),
            base_y=viewport_h * random.uniform(0.3, 0.5),
        )

        for dx, dy, delay in moves:
            if emulate and self.page:
                try:
                    await self.page.mouse.move(round(dx), round(dy), steps=1)
                except Exception:
                    pass
            await asyncio.sleep(delay)

    async def pause(self, min_seconds: float = 0.5, max_seconds: float = 2.0) -> None:
        """Random pause between actions to simulate thinking time."""
        await asyncio.sleep(random.uniform(min_seconds, max_seconds))

    async def human_wait(self, base: float = 1.0) -> None:
        """Wait a variable amount of time (normal distribution around base)."""
        delay = abs(random.gauss(base, base * 0.3))
        await asyncio.sleep(delay)


def _ease_in_out_cubic(t: float) -> float:
    """Smooth ease-in-out: accelerates, then decelerates."""
    if t < 0.5:
        return 4 * t**3
    return 1 - (-2 * t + 2) ** 3 / 2
