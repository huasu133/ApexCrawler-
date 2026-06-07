"""Novel crawling framework — multi-site unified novel downloader."""

from apexcrawler.novel.engine import NovelEngine
from apexcrawler.novel.adapter_base import SiteAdapter

__all__ = ["NovelEngine", "SiteAdapter"]
