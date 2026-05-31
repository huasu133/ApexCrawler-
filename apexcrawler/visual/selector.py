"""Visual point-and-click selector for ApexCrawler.

Launches a Playwright browser with an injected sidebar panel.
Users click elements on the page to define extraction fields.
Generates Pydantic schemas and XPath/CSS selectors automatically.
"""

from __future__ import annotations

import json
import logging
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright, Page, Browser

logger = logging.getLogger(__name__)

# ── Injected panel HTML/CSS/JS ──────────────────────────────

PANEL_HTML = """
<div id="apex-panel" style="
    position:fixed; top:0; right:0; width:360px; height:100vh;
    background:#1a1a2e; color:#e0e0e0; z-index:2147483647;
    font-family:system-ui,sans-serif; font-size:13px;
    box-shadow:-4px 0 20px rgba(0,0,0,0.5);
    display:flex; flex-direction:column;
">
    <!-- Header -->
    <div style="padding:16px; background:#16213e; border-bottom:1px solid #0f3460">
        <div style="font-size:15px; font-weight:700; color:#e94560">ApexCrawler</div>
        <div style="font-size:11px; color:#888; margin-top:2px">点击页面元素 → 定义提取字段</div>
    </div>

    <!-- Fields list -->
    <div id="apex-fields" style="flex:1; overflow-y:auto; padding:12px">
        <div style="color:#666; text-align:center; padding:40px 0; font-size:12px">
            点击页面中的元素开始提取
        </div>
    </div>

    <!-- Actions -->
    <div style="padding:12px; background:#16213e; border-top:1px solid #0f3460">
        <button id="apex-export" style="
            width:100%; padding:10px; background:#e94560; color:#fff;
            border:none; border-radius:6px; cursor:pointer;
            font-size:13px; font-weight:600;
            margin-bottom:6px;
        ">📋 导出提取规则</button>
        <button id="apex-preview" style="
            width:100%; padding:8px; background:#0f3460; color:#ccc;
            border:none; border-radius:6px; cursor:pointer;
            font-size:12px;
        ">🔍 预览提取数据</button>
        <button id="apex-clear" style="
            width:100%; padding:6px; background:transparent; color:#888;
            border:1px solid #333; border-radius:6px; cursor:pointer;
            font-size:11px; margin-top:6px;
        ">清除全部</button>
    </div>
</div>

<div id="apex-tooltip" style="
    position:fixed; z-index:2147483646; pointer-events:none;
    display:none; padding:4px 8px; background:rgba(233,69,96,0.9);
    color:#fff; font-size:11px; border-radius:4px;
    white-space:nowrap;
"></div>

<style>
    #apex-panel * { box-sizing:border-box; }
    .apex-field { background:#16213e; border-radius:6px; padding:10px; margin-bottom:8px; }
    .apex-field-name { color:#e94560; font-weight:600; font-size:12px; margin-bottom:4px; }
    .apex-field-selector { color:#888; font-size:11px; word-break:break-all; }
    .apex-field-value { color:#4ecca3; font-size:11px; margin-top:4px; }
    .apex-remove-btn { color:#e94560; cursor:pointer; float:right; font-size:14px; }
    .apex-highlight { outline:2px dashed #e94560 !important; outline-offset:2px; }
</style>

<script>
(function() {
    let fields = [];
    let hoveredEl = null;

    const panel = document.getElementById('apex-panel');
    const fieldsContainer = document.getElementById('apex-fields');
    const tooltip = document.getElementById('apex-tooltip');

    // Mouse tracking for element highlighting
    document.addEventListener('mousemove', function(e) {
        if (e.clientX > window.innerWidth - 380) { tooltip.style.display = 'none'; return; }
        const el = document.elementFromPoint(e.clientX, e.clientY);
        if (!el || el === hoveredEl || el.closest('#apex-panel')) return;

        if (hoveredEl) hoveredEl.classList.remove('apex-highlight');
        hoveredEl = el;
        hoveredEl.classList.add('apex-highlight');

        const tag = el.tagName.toLowerCase();
        const text = (el.textContent || '').trim().slice(0, 50);
        tooltip.textContent = `<${tag}> ${text}`;
        tooltip.style.display = 'block';
        tooltip.style.left = (e.clientX + 12) + 'px';
        tooltip.style.top = (e.clientY - 28) + 'px';
    });

    // Click to select
    document.addEventListener('click', function(e) {
        if (e.target.closest('#apex-panel')) return;
        e.preventDefault();
        e.stopPropagation();

        const el = e.target;
        const tag = el.tagName.toLowerCase();
        const text = (el.textContent || '').trim().slice(0, 100);
        const xpath = getXPath(el);
        const css = getCSSSelector(el);

        const fieldName = prompt('字段名称:', text.slice(0, 20) || tag);
        if (!fieldName) return;

        fields.push({
            name: fieldName,
            xpath: xpath,
            css: css,
            tag: tag,
            sample: text
        });
        renderFields();
    }, true);

    // XPath generator
    function getXPath(el) {
        if (el.id) return '//*[@id="' + el.id + '"]';
        const parts = [];
        while (el && el.nodeType === 1) {
            let idx = 1;
            let sibling = el.previousSibling;
            while (sibling) {
                if (sibling.nodeType === 1 && sibling.tagName === el.tagName) idx++;
                sibling = sibling.previousSibling;
            }
            parts.unshift(el.tagName.toLowerCase() + (idx > 1 ? '[' + idx + ']' : ''));
            el = el.parentNode;
        }
        return '/' + parts.join('/');
    }

    // CSS selector generator
    function getCSSSelector(el) {
        if (el.id) return '#' + el.id;
        const parts = [];
        while (el && el.tagName) {
            let selector = el.tagName.toLowerCase();
            if (el.className && typeof el.className === 'string') {
                const cls = el.className.trim().split(/\\s+/).filter(c => c && !c.startsWith('apex-')).slice(0, 2);
                if (cls.length) selector += '.' + cls.join('.');
            }
            parts.unshift(selector);
            el = el.parentNode;
            if (parts.length >= 3) break;  // max depth
        }
        return parts.join(' > ');
    }

    // Render field cards
    function renderFields() {
        if (fields.length === 0) {
            fieldsContainer.innerHTML = '<div style="color:#666;text-align:center;padding:40px 0;font-size:12px">点击页面中的元素开始提取</div>';
            return;
        }
        fieldsContainer.innerHTML = fields.map((f, i) => `
            <div class="apex-field">
                <span class="apex-remove-btn" onclick="window._apexRemove(${i})">✕</span>
                <div class="apex-field-name">📌 ${f.name}</div>
                <div class="apex-field-selector">${f.css}</div>
                <div class="apex-field-value">"${f.sample}"</div>
            </div>
        `).join('');
        window._apexFields = fields;
    }

    window._apexRemove = function(idx) {
        fields.splice(idx, 1);
        renderFields();
    };

    // Export
    document.getElementById('apex-export').addEventListener('click', function(e) {
        e.stopPropagation();
        const data = {fields: fields, url: window.location.href};
        window._apexExport = JSON.stringify(data, null, 2);
        alert('✅ 提取规则已生成！\n\n在终端中按 Enter 获取结果');
    });

    // Preview
    document.getElementById('apex-preview').addEventListener('click', function(e) {
        e.stopPropagation();
        if (fields.length === 0) { alert('请先点击页面元素定义字段'); return; }
        const result = {};
        fields.forEach(f => {
            const el = document.evaluate(f.xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
            result[f.name] = el ? (el.textContent || '').trim() : null;
        });
        alert('📊 预览结果:\n\n' + JSON.stringify(result, null, 2));
    });

    // Clear
    document.getElementById('apex-clear').addEventListener('click', function(e) {
        e.stopPropagation();
        fields = [];
        renderFields();
    });

    window._apexReady = true;
})();
</script>
"""


@dataclass
class VisualField:
    """A field selected by the user in visual mode."""

    name: str
    css_selector: str
    xpath: str
    tag: str = ""
    sample_text: str = ""


@dataclass
class VisualTemplate:
    """Complete extraction template from visual selection."""

    url: str
    fields: list[VisualField] = field(default_factory=list)
    pydantic_schema: str = ""  # Generated Pydantic model code
    apex_config: dict = field(default_factory=dict)  # Engine/proxy config


class VisualSelector:
    """Launch browser with injected point-and-click selector panel.

    Usage:
        selector = VisualSelector()
        template = await selector.start("https://example.com")
        # User clicks elements in browser, then presses Enter in terminal
        print(template.pydantic_schema)
    """

    def __init__(self, headless: bool = False):
        self._headless = headless
        self._browser: Browser | None = None
        self._page: Page | None = None

    async def start(self, url: str) -> VisualTemplate:
        """Open URL in browser with visual selector panel.

        User clicks elements to define fields.
        Press Enter in terminal to capture results.
        """
        pw = await async_playwright().start()
        self._browser = await pw.chromium.launch(
            headless=self._headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await self._browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        self._page = await ctx.new_page()

        # Navigate
        await self._page.goto(url, wait_until="networkidle", timeout=30000)
        await self._page.wait_for_timeout(1000)

        # Inject panel
        await self._page.evaluate(PANEL_HTML)

        print("\n" + "=" * 60)
        print("  ApexCrawler 可视化选择器")
        print("  🖱️  点击页面元素 → 输入字段名 → 定义提取规则")
        print("  📋 完成后点击「导出提取规则」，然后在终端按 Enter")
        print("=" * 60 + "\n")

        # Wait for user to finish
        await asyncio.get_event_loop().run_in_executor(None, input, "按 Enter 导出提取规则...")

        # Collect results
        try:
            raw = await self._page.evaluate("window._apexExport || '{}'")
            data = json.loads(raw)
        except Exception:
            data = {"fields": [], "url": url}

        fields = [
            VisualField(
                name=f.get("name", f"field_{i}"),
                css_selector=f.get("css", ""),
                xpath=f.get("xpath", ""),
                tag=f.get("tag", ""),
                sample_text=f.get("sample", ""),
            )
            for i, f in enumerate(data.get("fields", []))
        ]

        template = VisualTemplate(
            url=data.get("url", url),
            fields=fields,
            pydantic_schema=self._generate_schema(fields),
            apex_config={"engine": "vanilla", "tls_profile": "chrome_124"},
        )

        await self._browser.close()
        return template

    def _generate_schema(self, fields: list[VisualField]) -> str:
        """Generate a Pydantic model from selected fields."""
        if not fields:
            return ""

        lines = [
            "from pydantic import BaseModel",
            "",
            "",
            "class ExtractionSchema(BaseModel):",
        ]

        for f in fields:
            safe_name = f.name.replace(" ", "_").replace("-", "_").lower()
            lines.append(f'    {safe_name}: str  # CSS: {f.css_selector}')

        return "\n".join(lines)

    async def close(self):
        if self._browser:
            await self._browser.close()
