"""Data cleaning utilities for extracted web content.

Provides functions to normalize, sanitize, and standardize
raw text, HTML, prices, dates, and URLs after extraction.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Any
from html import unescape


# ── Text Cleaning ──────────────────────────────────────────

_WHITESPACE_RE = re.compile(r"\s+")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def clean_text(text: str, *, strip_html: bool = True, normalize_whitespace: bool = True) -> str:
    """Clean and normalize raw text extracted from HTML.

    Args:
        text: Raw text to clean.
        strip_html: Remove HTML tags.
        normalize_whitespace: Collapse multiple whitespace characters.

    Returns:
        Cleaned, normalized string.
    """
    if not text:
        return ""

    # Decode HTML entities first: &amp; → &, &#x2019; → ’, etc.
    text = unescape(text)

    # Remove control characters
    text = _CONTROL_CHARS_RE.sub("", text)

    # Strip HTML if requested
    if strip_html:
        text = _HTML_TAG_RE.sub(" ", text)

    # Normalize unicode
    text = unicodedata.normalize("NFKC", text)

    # Collapse whitespace
    if normalize_whitespace:
        text = _WHITESPACE_RE.sub(" ", text)

    return text.strip()


def strip_html(text: str) -> str:
    """Remove all HTML tags, leaving only text content."""
    return clean_text(text, strip_html=True)

# TRIM_HTML_FOR_LLM_ADDENDUM: supplement to AIExtractor._trim_html
# Use this function to trim arbitrary HTML for LLM context windows.
TRIM_TAGS_RE = re.compile(
    r"<(script|style|nav|footer|noscript|iframe|svg|canvas|video|audio)[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
ATTR_RE = re.compile(r'(\s)(?:class|style|data-\w+|on\w+|aria-\w+|role|tabindex|id)\s*=\s*"[^"]*"', re.IGNORECASE)


def trim_html_for_llm(html: str, *, max_chars: int = 8000, strip_attrs: bool = True) -> str:
    """Aggressively reduce HTML size for LLM context windows.

    Removes non-semantic tags, comments, and optionally inline attributes.

    Args:
        html: Full page HTML.
        max_chars: Maximum character count for output.
        strip_attrs: Remove class, style, data-*, event handler attributes.

    Returns:
        Trimmed HTML string suitable for LLM prompts.
    """
    # Strip comments
    html = COMMENT_RE.sub("", html)
    # Strip non-semantic tags
    html = TRIM_TAGS_RE.sub("", html)
    # Optionally strip inline attributes
    if strip_attrs:
        html = ATTR_RE.sub("", html)
    # Unescape for cleaner text
    html = unescape(html)
    # Collapse whitespace
    html = _WHITESPACE_RE.sub(" ", html).strip()
    return html[:max_chars] if len(html) > max_chars else html


# ── Price Cleaning ─────────────────────────────────────────

_PRICE_RE = re.compile(r"[\d,]+(?:\.\d{1,2})?")
_CURRENCY_SYMBOLS: dict[str, str] = {
    "$": "USD", "€": "EUR", "£": "GBP", "¥": "CNY",
    "元": "CNY", "kr": "SEK", "₩": "KRW", "₹": "INR",
    "R$": "BRL", "A$": "AUD", "C$": "CAD", "₽": "RUB",
    "د.إ": "AED", "﷼": "SAR", "₫": "VND", "₱": "PHP",
    "NT$": "TWD", "HK$": "HKD", "S$": "SGD", "CHF": "CHF",
}
_CURRENCY_CODE_RE = re.compile(r"\b(USD|EUR|GBP|JPY|CNY|KRW|INR|BRL|AUD|CAD)\b")
# Japanese indicators: .jp domain, 円 character, kana ranges
_JP_INDICATOR_RE = re.compile(r"(\.jp\b|[ぁ-んァ-ン])")


def clean_price(text: str, *, context: str = "") -> tuple[float | None, str]:
    """Extract numeric price and currency from a raw price string.

    Args:
        text: Raw price string like "$19.99", "EUR 42.50", "1,299元".
        context: Optional contextual text (e.g. page URL, surrounding HTML)
                 used to disambiguate ¥ between CNY and JPY.

    Returns:
        Tuple of (float price or None, currency code defaulting to "USD").
    """
    if not text:
        return None, "USD"

    text = text.strip()

    # Detect currency code first
    currency = "USD"
    code_match = _CURRENCY_CODE_RE.search(text)
    if code_match:
        currency = code_match.group(0)
        text = _CURRENCY_CODE_RE.sub("", text, count=1)

    # Detect currency symbol
    if text:
        has_yen = False
        for symbol, code in sorted(_CURRENCY_SYMBOLS.items(), key=lambda x: -len(x[0])):
            if text.startswith(symbol) or text.endswith(symbol):
                if symbol == "¥":
                    has_yen = True
                currency = code
                text = text.replace(symbol, "").strip()
                break

        # Disambiguate ¥: default CNY, only JPY if Japanese context detected
        if has_yen and currency == "CNY" and context:
            if _JP_INDICATOR_RE.search(context):
                currency = "JPY"

    # Extract numeric value
    num_match = _PRICE_RE.search(text)
    if not num_match:
        return None, currency

    raw_num = num_match.group(0).replace(",", "")
    try:
        price = float(raw_num)
    except ValueError:
        return None, currency

    return round(price, 2), currency


# ── Date Cleaning ──────────────────────────────────────────

_DATE_FORMATS = [
    # ISO 8601 first
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})([+-]\d{2}:?\d{2}|Z)?"), "iso"),
    # Common date formats
    (re.compile(r"(\d{1,2})\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{4})", re.I), "us_short"),
    (re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})"), "ymd"),
    (re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})"), "dmy_mdy"),
    # Relative
    (re.compile(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", re.I), "relative"),
]

_MONTH_MAP = {name[:3].lower(): i for i, name in enumerate([
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
], start=1)}


def clean_date(text: str) -> datetime | None:
    """Parse a date string into a timezone-aware UTC datetime.

    Args:
        text: Raw date string like "2024-01-15", "3 hours ago", "Jan 5, 2024".

    Returns:
        UTC datetime or None if parsing fails.
    """
    if not text:
        return None

    text = text.strip()

    for pattern, fmt_type in _DATE_FORMATS:
        m = pattern.match(text)
        if not m:
            continue

        if fmt_type == "iso":
            year, month, day, hour, minute, second = map(int, m.groups()[:6])
            return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)

        elif fmt_type == "us_short":
            day, month_name, year = m.group(1), m.group(2)[:3].lower(), m.group(3)
            return datetime(int(year), _MONTH_MAP[month_name], int(day), tzinfo=timezone.utc)

        elif fmt_type == "ymd":
            year = int(m.group(1))
            month = min(12, max(1, int(m.group(2))))
            day = min(31, max(1, int(m.group(3))))
            return datetime(year, month, day, tzinfo=timezone.utc)

        elif fmt_type == "dmy_mdy":
            part1, part2, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            # Heuristic: first part > 12 ⇒ DMY
            if part1 > 12:
                day, month = part1, part2
            else:
                month, day = part1, part2
            return datetime(year, min(12, max(1, month)), min(31, max(1, day)), tzinfo=timezone.utc)

        elif fmt_type == "relative":
            amount = int(m.group(1))
            unit = m.group(2).lower()
            now = datetime.now(timezone.utc)
            delta_map = {
                "minute": timedelta(minutes=amount),
                "hour": timedelta(hours=amount),
                "day": timedelta(days=amount),
                "week": timedelta(weeks=amount),
                "month": timedelta(days=amount * 30),
                "year": timedelta(days=amount * 365),
            }
            return now - delta_map.get(unit, timedelta())

    return None


# ── URL Cleaning ───────────────────────────────────────────

_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "_ga", "gclsrc", "dclid", "msclkid",
    "mc_cid", "mc_eid", "igshid",
})


def clean_url(url: str, *, base_url: str = "") -> str:
    """Normalize a URL: strip fragments, trailing slashes, tracking params.

    Args:
        url: Raw URL string.
        base_url: Optional base URL for resolving relative paths.

    Returns:
        Cleaned, normalized URL string.
    """
    if not url:
        return ""

    from urllib.parse import urlparse, urljoin, urlunparse, parse_qs, urlencode

    # Resolve relative URLs
    if base_url and not url.startswith(("http://", "https://")):
        url = urljoin(base_url, url)

    parsed = urlparse(url)

    # Strip tracking params from query string
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    clean_params = {k: v for k, v in query_params.items()
                    if k not in _TRACKING_PARAMS}
    clean_query = urlencode(clean_params, doseq=True)

    # Reconstruct without fragment
    clean = urlunparse((
        parsed.scheme or "https",
        parsed.netloc.lower() if parsed.netloc else "",
        parsed.path.rstrip("/") or "/",
        parsed.params,
        clean_query,
        "",  # fragment
    ))

    return clean


# ── Batch Cleaning ─────────────────────────────────────────

def clean_record(data: dict[str, Any]) -> dict[str, Any]:
    """Apply text, price, date, and URL cleaning to a dict in-place.

    Recognizes common field name patterns and applies appropriate cleaners.

    Args:
        data: Dictionary of extracted fields.

    Returns:
        Same dict with values cleaned.
    """
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        if not isinstance(value, str):
            cleaned[key] = value
            continue

        kl = key.lower()
        if any(kw in kl for kw in ("price", "amount", "cost", "fee")):
            price, curr = clean_price(value)
            cleaned[key] = price
            cleaned[f"{key}_currency"] = curr
        elif any(kw in kl for kw in ("date", "time", "created", "updated", "published", "modified")):
            cleaned[key] = clean_date(value)
        elif "url" in kl or "link" in kl or kl.endswith("_href"):
            cleaned[key] = clean_url(value)
        elif "title" in kl:
            # Title fields: preserve raw text, no HTML stripping
            cleaned[key] = clean_text(value, strip_html=False)
        elif any(kw in kl for kw in ("text", "body", "description", "summary")):
            cleaned[key] = clean_text(value)
        else:
            cleaned[key] = clean_text(value) if len(value) > 2 else value

    return cleaned


class Cleaner:
    """Pipeline-compatible data cleaner for post-extraction normalization."""

    def clean(self, html: str) -> str:
        """Clean and normalize extracted HTML content.

        Removes excess whitespace, scripts, comments, and normalizes text.
        """
        if not html:
            return html
        # Remove HTML comments
        html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
        # Remove script/style tags
        for tag in ("script", "style", "noscript"):
            html = re.sub(
                f"<{tag}[^>]*>.*?</{tag}>",
                "",
                html,
                flags=re.DOTALL | re.IGNORECASE,
            )
        # Normalize whitespace
        html = re.sub(r"\s+", " ", html)
        return html.strip()
