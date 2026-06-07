"""Pydantic settings models for ApexCrawler configuration.

Supports nested environment variables with APEX_ prefix and
YAML file loading via pydantic-settings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EngineConfig(BaseModel):
    """Configuration for a single browser engine instance."""

    type: Literal["vanilla", "patched", "camoufox", "cloaked"] = "vanilla"
    headless: bool = True
    max_concurrent: int = Field(default=1, ge=1, le=10)
    timeout_seconds: int = Field(default=30, ge=5, le=300)
    viewport: dict = Field(default_factory=lambda: {"width": 1920, "height": 1080})
    extra_args: list[str] = Field(default_factory=list)


class ProxyPoolConfig(BaseModel):
    """Configuration for the proxy pool subsystem."""

    providers: list[str] = Field(default_factory=list, deprecated="Not implemented — use proxy sources plugin instead")
    min_pool_size: int = 5
    health_check_interval: int = 60
    cooldown_seconds: int = 300
    geo_preferences: dict[str, int] = Field(default_factory=dict)


class CacheConfig(BaseModel):
    """Configuration for the caching subsystem."""

    backend: Literal["redis", "memory"] = "memory"
    redis_url: Optional[SecretStr] = None
    ttl_seconds: int = 3600


class LLMConfig(BaseModel):
    """Configuration for LLM-based extraction / decision engines."""

    provider: Literal["openai", "claude", "ollama", "deepseek"] = "ollama"
    api_key: Optional[SecretStr] = None
    model: str = "qwen2.5:3b"
    base_url: str = "http://localhost:11434/v1"
    max_tokens: int = 1024
    temperature: float = 0.1


class PipelineConfig(BaseModel):
    """Configuration for the crawl pipeline."""

    max_concurrent_tasks: int = 10
    retry_max: int = 3
    retry_base_delay: float = 1.0
    stage_timeouts: dict = Field(default_factory=dict)


class Settings(BaseSettings):
    """Root settings — loaded from YAML + environment variables.

    Environment variables use the APEX_ prefix with __ as nested delimiter.
    Example: APEX_ENGINES__VANILLA__HEADLESS=false
    """

    model_config = SettingsConfigDict(
        env_prefix="APEX_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    engines: dict[str, EngineConfig] = Field(default_factory=dict)
    proxy: ProxyPoolConfig = Field(default_factory=ProxyPoolConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @classmethod
    def from_yaml(cls, path: str | Path = "config/base.yaml") -> Settings:
        """Load settings from a YAML file, falling back to environment variables.

        Args:
            path: Path to YAML configuration file (default: config/base.yaml)

        Returns:
            Settings instance with values merged from YAML + env vars.
        """
        yaml_path = Path(path)
        if not yaml_path.exists():
            return cls()
        try:
            import yaml
        except ImportError:
            return cls()
        try:
            yaml_data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return cls()
        return cls(**yaml_data)

    @model_validator(mode="before")
    @classmethod
    def _load_yaml_fallback(cls, values: Any) -> Any:
        """Explicitly load YAML config as fallback for pydantic-settings < 2.0
        that may not support the ``yaml_file`` config key."""
        if not isinstance(values, dict):
            return values
        yaml_path = Path("config/base.yaml")
        if not yaml_path.exists():
            return values
        try:
            import yaml
        except ImportError:
            return values
        try:
            yaml_data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return values
        if isinstance(yaml_data, dict):
            for k, v in yaml_data.items():
                if k not in values:
                    values[k] = v
        return values

    @field_validator("engines")
    @classmethod
    def at_least_one_engine(cls, v: dict[str, EngineConfig]) -> dict[str, EngineConfig]:
        if not v:
            raise ValueError("At least one engine must be configured")
        return v
