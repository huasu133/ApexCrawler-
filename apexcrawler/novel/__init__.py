"""Novel crawling framework — multi-site unified novel downloader."""

# 导入适配器模块，触发 @register_adapter 装饰器注册
from apexcrawler.novel import adapter_qidian  # noqa: F401
from apexcrawler.novel import adapter_17k      # noqa: F401
from apexcrawler.novel import adapter_biquge   # noqa: F401

from apexcrawler.novel.engine import NovelEngine
from apexcrawler.novel.adapter_base import SiteAdapter, Chapter, BookInfo

__all__ = ["NovelEngine", "SiteAdapter", "Chapter", "BookInfo"]
