"""Template recorder: save/load visual extraction templates.

Templates are YAML files that record:
- Target URL patterns
- Field definitions (CSS/XPath selectors)
- Engine and proxy preferences
- Pydantic schema code
"""

from __future__ import annotations

import os
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path.home() / ".apexcrawler" / "templates"


@dataclass
class TemplateField:
    name: str
    css: str = ""
    xpath: str = ""
    type: str = "str"  # str, int, float, bool
    sample: str = ""


@dataclass
class Template:
    """Saved extraction template."""

    name: str
    url_pattern: str  # e.g. "*.amazon.com/*/dp/*"
    fields: list[TemplateField] = field(default_factory=list)
    engine: str = "vanilla"
    tls_profile: str = "chrome_124"
    proxy_type: str = "none"  # none, residential, datacenter
    pydantic_schema: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Template":
        fields = [TemplateField(**f) for f in data.pop("fields", [])]
        return cls(fields=fields, **data)


class TemplateStore:
    """Persistent template storage with JSON files."""

    def __init__(self, templates_dir: Path | None = None):
        self._dir = templates_dir or TEMPLATES_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, template: Template) -> str:
        """Save template to JSON file. Returns file path."""
        filename = template.name.replace(" ", "_").lower() + ".json"
        filepath = self._dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(template.to_dict(), f, indent=2, ensure_ascii=False)

        logger.info(f"Template saved: {filepath}")
        return str(filepath)

    def load(self, name: str) -> Template | None:
        """Load template by name."""
        filename = name.replace(" ", "_").lower()
        if not filename.endswith(".json"):
            filename += ".json"
        filepath = self._dir / filename

        if not filepath.exists():
            return None

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        return Template.from_dict(data)

    def list_all(self) -> list[str]:
        """List all saved template names."""
        return [
            f.stem
            for f in self._dir.glob("*.json")
            if f.is_file()
        ]

    def delete(self, name: str) -> bool:
        """Delete a template. Returns True if deleted."""
        filename = name.replace(" ", "_").lower() + ".json"
        filepath = self._dir / filename

        if filepath.exists():
            filepath.unlink()
            return True
        return False

    def match_url(self, url: str) -> Template | None:
        """Find first template matching the given URL."""
        import fnmatch

        for template_file in self._dir.glob("*.json"):
            with open(template_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            pattern = data.get("url_pattern", "")
            if pattern and fnmatch.fnmatch(url, pattern):
                return Template.from_dict(data)

        return None


# ── Built-in templates ─────────────────────────────────────

BUILTIN_TEMPLATES = [
    Template(
        name="Amazon Product",
        url_pattern="*.amazon.com/*/dp/*",
        fields=[
            TemplateField(name="title", css="#productTitle", xpath="//*[@id='productTitle']", sample="Product Title"),
            TemplateField(name="price", css=".a-price .a-offscreen", xpath="//span[contains(@class,'a-price')]//span[contains(@class,'a-offscreen')]", type="str"),
            TemplateField(name="rating", css="#acrPopover span.a-size-base", xpath="//*[@id='acrPopover']//span[contains(@class,'a-size-base')]", type="str"),
        ],
        engine="cloaked",
        tls_profile="chrome_124",
        proxy_type="residential",
        tags=["ecommerce", "amazon"],
    ),
    Template(
        name="Google Maps Place",
        url_pattern="*.google.com/maps/place/*",
        fields=[
            TemplateField(name="place_name", css="h1", xpath="//h1", sample="Place Name"),
            TemplateField(name="address", css="button[data-item-id='address']", xpath="//button[@data-item-id='address']", type="str"),
            TemplateField(name="rating", css="div.fontDisplayLarge", xpath="//div[contains(@class,'fontDisplayLarge')]", type="str"),
        ],
        engine="camoufox",
        tls_profile="chrome_131",
        tags=["maps", "local"],
    ),
]


def ensure_builtin_templates():
    """Install built-in templates if not already present."""
    store = TemplateStore()
    for t in BUILTIN_TEMPLATES:
        if not store.load(t.name):
            store.save(t)
