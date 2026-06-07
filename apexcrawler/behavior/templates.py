"""Behavior templates: pre-composed action sequences.

Templates orchestrate Humanizer actions into common browsing patterns.
Each template returns an async callable that the pipeline can invoke.
"""

from __future__ import annotations

import asyncio
import random
from typing import Awaitable, Callable

from .humanizer import Humanizer

BehaviorSequence = Callable[[], Awaitable[None]]


class BehaviorTemplate:
    """Base class for behavior templates."""

    name: str = ""

    def __init__(self, humanizer: Humanizer):
        self._humanizer = humanizer

    async def execute(self) -> None:
        raise NotImplementedError


class IdleBrowsingTemplate(BehaviorTemplate):
    """Simulate a user casually browsing a page.

    1. Load the page (warm-up already done)
    2. Move mouse around header/nav area
    3. Scroll down slowly, pausing at content sections
    4. Move mouse to a random link/location
    5. Pause (simulating reading)
    """

    name = "idle_browsing"

    async def execute(self) -> None:
        h = self._humanizer

        # Move mouse to top-left (typical starting position after page load)
        await h.mouse.move_to(
            random.uniform(100, 300),
            random.uniform(80, 200),
            steps=15,
        )
        await h.pause(0.5, 1.5)

        # Scroll down in segments with reading pauses
        for _ in range(random.randint(2, 4)):
            await h.scroll.scroll("down", distance=random.randint(300, 700))
            await h.mouse.move_to(
                random.uniform(200, 800),
                random.uniform(300, 600),
                steps=10,
            )
            await h.pause(0.8, 3.0)

        # Move to bottom as if reaching end of page
        await h.mouse.move_to(
            random.uniform(400, 800),
            random.uniform(600, 900),
            steps=20,
        )
        await h.pause(1.0, 3.0)


class SearchAndTypeTemplate(BehaviorTemplate):
    """Simulate searching and typing on a page.

    1. Move mouse to a search input area
    2. Click to focus
    3. Type a query with human-like timing
    4. Pause before submitting
    """

    name = "search_and_type"

    def __init__(self, humanizer: Humanizer, query: str = ""):
        super().__init__(humanizer)
        self.query = query

    async def execute(self) -> None:
        h = self._humanizer

        # Move to search box area (typically top-right)
        search_x = random.uniform(400, 700)
        search_y = random.uniform(60, 120)
        await h.mouse.move_to(search_x, search_y, steps=12)

        # Click to focus
        await h.mouse.click()
        await h.pause(0.3, 0.8)

        # Type the query
        text = self.query or "test search query"
        await h.keyboard.type(text)

        # Pause as if reviewing
        await h.pause(0.8, 2.0)

        # Press Enter
        if h.page:
            try:
                await h.page.keyboard.press("Enter")  # type: ignore
            except Exception:
                pass


class FillFormTemplate(BehaviorTemplate):
    """Simulate filling a web form.

    1. Move to first field, click, type
    2. Tab between fields
    3. Click and type in each field
    4. Move to submit button area
    """

    name = "fill_form"

    def __init__(self, humanizer: Humanizer, fields: list[tuple[str, str]] | None = None):
        """Args:
            humanizer: The Humanizer instance.
            fields: List of (label, value) pairs to fill.
        """
        super().__init__(humanizer)
        self.fields = fields or []

    async def execute(self) -> None:
        h = self._humanizer

        for i, (label, value) in enumerate(self.fields):
            # Move to field area (simulated positions)
            field_x = random.uniform(200, 500)
            field_y = 200 + i * 80
            await h.mouse.move_to(field_x, field_y, steps=10)

            # Click to focus
            await h.mouse.click()
            await h.pause(0.2, 0.5)

            # Type the value
            await h.keyboard.type(value)
            await h.pause(0.3, 0.8)

        # Move to submit button area
        await h.mouse.move_to(
            random.uniform(300, 600),
            200 + len(self.fields) * 80 + random.uniform(30, 80),
            steps=15,
        )
        await h.pause(0.5, 1.5)


class ContentConsumptionTemplate(BehaviorTemplate):
    """Simulate reading/consuming content on a long page.

    1. Scroll through content section by section
    2. Occasionally move mouse to scrollbar area
    3. Pause at images or dense text areas
    4. Reach bottom naturally
    """

    name = "content_consumption"

    async def execute(self) -> None:
        h = self._humanizer

        # Warm-up: position mouse near content area
        await h.mouse.move_to(
            random.uniform(500, 900),
            random.uniform(200, 400),
            steps=15,
        )
        await h.pause(0.5, 1.0)

        # Progressive scroll with content consumption pauses
        scrolls = random.randint(4, 8)
        for _ in range(scrolls):
            # Scroll a chunk
            await h.scroll.scroll("down", distance=random.randint(300, 600))

            # Move mouse as if tracking content
            await h.mouse.move_to(
                random.uniform(300, 900),
                random.uniform(350, 700),
                steps=8,
            )

            # "Reading" pause — longer for denser content (random)
            if random.random() < 0.4:
                await h.pause(2.0, 6.0)
            else:
                await h.pause(0.5, 2.0)

        # Scroll to bottom
        await h.scroll.scroll_to_bottom(max_scrolls=random.randint(3, 6))


# ── Template registry ──────────────────────────────────────────


def create_sequence(
    humanizer: Humanizer,
    template_name: str,
    **kwargs,
) -> BehaviorTemplate:
    """Factory for behavior templates.

    Args:
        humanizer: The Humanizer instance.
        template_name: One of "idle_browsing", "search_and_type", "fill_form", "content_consumption".
        **kwargs: Template-specific arguments.

    Returns:
        A BehaviorTemplate instance ready to execute.
    """
    registry: dict[str, type[BehaviorTemplate]] = {
        "idle_browsing": IdleBrowsingTemplate,
        "search_and_type": SearchAndTypeTemplate,
        "fill_form": FillFormTemplate,
        "content_consumption": ContentConsumptionTemplate,
    }

    cls = registry.get(template_name)
    if cls is None:
        raise ValueError(f"Unknown template: {template_name}. Available: {list(registry)}")

    return cls(humanizer, **kwargs)  # type: ignore


async def execute_behavior(
    humanizer: Humanizer,
    template_name: str,
    **kwargs,
) -> None:
    """Execute a named behavior template.

    Convenience function that creates and runs a template in one call.
    """
    template = create_sequence(humanizer, template_name, **kwargs)
    await template.execute()


# ── Template metadata registry ──────────────────────────────────


TEMPLATES: dict[str, dict] = {
    "idle_browsing": {
        "name": "idle_browsing",
        "description": "Casual page browsing with mouse movement and scrolling",
        "class": "IdleBrowsingTemplate",
        "profiles": [],
    },
    "search_and_type": {
        "name": "search_and_type",
        "description": "Search query input with human-like typing",
        "class": "SearchAndTypeTemplate",
        "profiles": [],
    },
    "fill_form": {
        "name": "fill_form",
        "description": "Form filling with field-by-field input",
        "class": "FillFormTemplate",
        "profiles": [],
    },
    "content_consumption": {
        "name": "content_consumption",
        "description": "Long-form content reading with progressive scrolling",
        "class": "ContentConsumptionTemplate",
        "profiles": [],
    },
    "novel_reader": {
        "name": "novel_reader",
        "description": "Web novel reader — simulates reading chapters with realistic pacing",
        "class": "NovelReaderTemplate",
        "profiles": ["engrossed", "skimmer", "relaxed"],
    },
}
