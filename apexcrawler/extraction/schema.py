"""Common extraction schemas for web scraping.

Pre-built Pydantic models for common scraping targets:
products, articles, search results, reviews, and company data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field, field_validator


# ── Product ─────────────────────────────────────────────────

class ProductVariant(BaseModel):
    """Product variant: size, color, SKU variation."""
    name: str = ""
    sku: str = ""
    price: float | None = None
    currency: str = "USD"
    availability: bool = True
    attributes: dict[str, str] = Field(default_factory=dict)


class Product(BaseModel):
    """E-commerce product listing."""
    title: str
    description: str = ""
    price: float | None = None
    currency: str = "USD"
    original_price: float | None = None
    currency_original: str = "USD"
    brand: str = ""
    category: str = ""
    subcategory: str = ""
    sku: str = ""
    mpn: str = ""
    gtin: str = ""
    availability: str = ""  # "InStock", "OutOfStock", "PreOrder"
    rating: float | None = None
    review_count: int = 0
    image_urls: list[str] = Field(default_factory=list)
    product_url: str = ""
    variants: list[ProductVariant] = Field(default_factory=list)
    specs: dict[str, str] = Field(default_factory=dict)
    seller: str = ""
    shipping_info: str = ""

    @field_validator("rating")
    @classmethod
    def clamp_rating(cls, v: float | None) -> float | None:
        if v is not None:
            return max(0.0, min(5.0, round(v, 1)))
        return v


# ── Article ─────────────────────────────────────────────────

class Article(BaseModel):
    """News article or blog post."""
    headline: str = Field(description="Title or headline of the article")
    author: str = ""
    published_date: datetime | None = None
    modified_date: datetime | None = None
    body_text: str = ""
    summary: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    image_url: str = ""
    source_url: str = ""
    word_count: int = 0
    reading_time_minutes: int = 0
    language: str = ""


# ── Search Result ───────────────────────────────────────────

class SearchResult(BaseModel):
    """Single search engine result item."""
    title: str
    url: str
    snippet: str = ""
    display_url: str = ""
    position: int = 0
    is_ad: bool = False
    domain: str = ""
    favicon_url: str = ""


class SearchPage(BaseModel):
    """Complete search engine results page."""
    query: str
    total_results: int = 0
    results: list[SearchResult] = Field(default_factory=list)
    related_queries: list[str] = Field(default_factory=list)
    pagination: dict[str, Any] = Field(default_factory=dict)
    search_engine: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ── Review ──────────────────────────────────────────────────

class Review(BaseModel):
    """User review for a product/service."""
    title: str = ""
    body: str
    rating: float = 0.0
    author: str = ""
    date: datetime | None = None
    verified_purchase: bool = False
    helpful_count: int = 0
    images: list[str] = Field(default_factory=list)
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)

    @field_validator("rating")
    @classmethod
    def clamp_review_rating(cls, v: float) -> float:
        return max(0.0, min(5.0, round(v, 1)))


class ReviewPage(BaseModel):
    """Collection of reviews with aggregate stats."""
    product_name: str = ""
    product_url: str = ""
    average_rating: float = 0.0
    total_reviews: int = 0
    rating_distribution: dict[int, int] = Field(default_factory=dict)
    reviews: list[Review] = Field(default_factory=list)


# ── Company / Organization ──────────────────────────────────

class Company(BaseModel):
    """Company profile scraped from business directories."""
    name: str
    website: str = ""
    industry: str = ""
    headquarters: str = ""
    founded: int | None = None
    employee_count: int | None = None
    annual_revenue: str = ""
    description: str = ""
    linkedin_url: str = ""
    twitter_handle: str = ""
    logo_url: str = ""
    domains: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    funding_total: str = ""
    stock_symbol: str = ""


# ── Generic / Free-form ─────────────────────────────────────

class GenericEntity(BaseModel):
    """Catch-all schema for arbitrary key-value extraction."""
    entity_type: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)
    source_url: str = ""
    extracted_at: datetime = Field(default_factory=datetime.utcnow)


# ── Schema registry ─────────────────────────────────────────

_SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {
    "product": Product,
    "article": Article,
    "search_result": SearchResult,
    "search_page": SearchPage,
    "review": Review,
    "review_page": ReviewPage,
    "company": Company,
    "generic": GenericEntity,
}


def get_schema(name: str) -> type[BaseModel] | None:
    """Look up a pre-built schema by name."""
    return _SCHEMA_REGISTRY.get(name.lower())


def register_schema(name: str, schema: type[BaseModel]) -> None:
    """Register a custom schema for reuse."""
    _SCHEMA_REGISTRY[name.lower()] = schema


def list_schemas() -> list[str]:
    """List all registered schema names."""
    return sorted(_SCHEMA_REGISTRY.keys())
