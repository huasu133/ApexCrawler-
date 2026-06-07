"""Crawl4AI LLM extraction and content filtering integration.

Wraps Crawl4AI's LLMExtractionStrategy and content filters for use
within ApexCrawler's pipeline and CLI.
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    """Configuration for LLM extraction.

    Example:
        config = LLMConfig(
            provider="openai/gpt-4o",
            api_token=os.getenv("OPENAI_API_KEY"),
            schema=Product.model_json_schema(),
            instruction="Extract all products with name and price",
        )
    """
    provider: str = "openai/gpt-4o"
    api_token: str = ""
    model: Optional[str] = None
    schema: Optional[Dict] = None
    instruction: str = ""
    input_format: str = "markdown"
    chunk_token_threshold: int = 4000
    extraction_type: str = "schema"

    @classmethod
    def from_json(cls, schema_json: Optional[str] = None) -> "LLMConfig":
        """Parse schema from a JSON string."""
        inst = cls()
        if schema_json:
            try:
                inst.schema = json.loads(schema_json)
            except json.JSONDecodeError:
                raise ValueError(f"Invalid JSON schema: {schema_json[:100]}...")
        return inst


@dataclass
class ContentFilterConfig:
    """Configuration for content filtering."""
    filter_type: str = ""  # "", "bm25", "pruning"
    user_query: str = ""
    bm25_threshold: float = 1.0
    pruning_threshold: float = 0.48
    min_word_threshold: int = 0


# ── LLM Extraction ──────────────────────────────────────────

def extract_with_llm(html: str, config: LLMConfig) -> Dict[str, Any]:
    """Extract structured data from HTML using Crawl4AI's LLMExtractionStrategy.

    Args:
        html: Raw HTML content to extract from.
        config: LLM extraction configuration.

    Returns:
        Extracted structured data as dict.

    Raises:
        ImportError: If crawl4ai is not installed.
        ValueError: If extraction fails.
    """
    try:
        from crawl4ai import LLMExtractionStrategy, LLMConfig as Crawl4AILLMConfig
    except ImportError:
        raise ImportError(
            "crawl4ai not available. Install: pip install crawl4ai"
        )

    llm_cfg = Crawl4AILLMConfig(
        provider=config.provider,
        api_token=config.api_token or "",
    )
    if config.model:
        llm_cfg.model = config.model

    strategy = LLMExtractionStrategy(
        llm_config=llm_cfg,
        schema=config.schema,
        instruction=config.instruction,
        input_format=config.input_format,
        chunk_token_threshold=config.chunk_token_threshold,
        extraction_type=config.extraction_type,
        verbose=False,
    )

    try:
        result = strategy.extract(html)
        return {"success": True, "data": result}
    except Exception as e:
        logger.warning(f"LLM extraction failed: {e}")
        return {"success": False, "error": str(e)}


# ── Content Filtering ───────────────────────────────────────

def filter_content(html: str, config: ContentFilterConfig) -> str:
    """Filter HTML content using Crawl4AI's content filters to produce cleaner markdown.

    Args:
        html: Raw HTML content.
        config: Content filter configuration.

    Returns:
        Cleaned markdown text, or empty string if filtering fails.
    """
    if not html or len(html) < 500:
        return ""

    try:
        from crawl4ai.content_filter_strategy import PruningContentFilter, BM25ContentFilter
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    except ImportError:
        logger.debug("crawl4ai not available, skipping content filter")
        return ""

    try:
        # Select filter based on config
        if config.filter_type == "bm25" and config.user_query:
            filter_inst = BM25ContentFilter(
                user_query=config.user_query,
                bm25_threshold=config.bm25_threshold,
            )
        elif config.filter_type == "pruning":
            filter_inst = PruningContentFilter(
                threshold=config.pruning_threshold,
                threshold_type="fixed",
                min_word_threshold=config.min_word_threshold,
            )
        else:
            # Default: use pruning
            filter_inst = PruningContentFilter(
                threshold=0.48,
                threshold_type="fixed",
                min_word_threshold=0,
            )

        md_generator = DefaultMarkdownGenerator(content_filter=filter_inst)
        result = md_generator.generate_markdown(html)
        return result.markdown or ""

    except Exception as e:
        logger.debug(f"Content filtering failed: {e}")
        return ""


# ── Utility: check crawl4ai availability ────────────────────

def is_crawl4ai_available() -> bool:
    """Check if crawl4ai is installed."""
    try:
        import crawl4ai  # noqa: F401
        return True
    except ImportError:
        return False
