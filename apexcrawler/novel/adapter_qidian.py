"""Qidian novel site adapter — wraps existing QidianEngine."""
from __future__ import annotations
import logging, os, re, time
from typing import List, Optional
from apexcrawler.novel.adapter_base import SiteAdapter, BookInfo, Chapter
from apexcrawler.novel.engine import register_adapter

logger = logging.getLogger(__name__)


@register_adapter
class QidianAdapter(SiteAdapter):
    """Adapter for Qidian.com using the existing QidianEngine."""

    URL_PATTERNS = [
        r"book\.qidian\.com/info/(\d+)",
        r"www\.qidian\.com/book/(\d+)",
        r"www\.qidian\.com/chapter/(\d+)",
        r"qidian\.com/(?:book|info|chapter)/(\d+)",
    ]

    def __init__(self):
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            from apexcrawler.engines.qidian import QidianEngine
            self._engine = QidianEngine(headless=True)
        return self._engine

    def match(self, url: str) -> bool:
        import re
        return any(re.search(p, url) for p in self.URL_PATTERNS)

    def _extract_book_id(self, url: str) -> int:
        import re
        for p in self.URL_PATTERNS:
            m = re.search(p, url)
            if m:
                return int(m.group(1))
        raise ValueError(f"Cannot extract book_id from: {url}")

    def get_book_info(self, url: str) -> BookInfo:
        book_id = self._extract_book_id(url)
        qidian_chapters = self.engine.fetch_catalog(book_id)
        chapters = [
            Chapter(
                index=c.index,
                title=c.title,
                chapter_id=str(c.chapter_id),
                is_vip=c.is_vip,
                word_count=c.word_count,
                url=c.url,
            )
            for c in qidian_chapters
        ]
        # 从引擎获取书名等信息
        title = getattr(self.engine, "_last_book_title", "") or f"Book {book_id}"
        description = getattr(self.engine, "_last_book_description", "")
        return BookInfo(
            book_id=str(book_id),
            title=title,
            description=description,
            chapters=chapters,
        )

    def fetch_chapter(self, chapter: Chapter) -> str:
        from apexcrawler.engines.qidian import Chapter as QChapter
        qc = QChapter(
            chapter_id=int(chapter.chapter_id) if chapter.chapter_id else 0,
            book_id=0,
            title=chapter.title,
            index=chapter.index,
            url=chapter.url,
        )
        result = self.engine.fetch_chapter(qc)
        return result.content or ""

    def download(self, book: BookInfo, chapters: List[Chapter], output: str = "txt") -> str:
        from apexcrawler.engines.qidian import Chapter as QChapter

        # 书名做文件名
        safe_title = re.sub(r'[\\/:*?"<>|]', '_', book.title or book.book_id)
        filename = f"{safe_title}_{int(time.time())}.{output}"

        # 转换为引擎内部的 Chapter 对象
        engine_chapters = [
            QChapter(
                chapter_id=int(ch.chapter_id) if ch.chapter_id else 0,
                book_id=int(book.book_id),
                title=ch.title,
                index=ch.index,
                is_vip=ch.is_vip,
                url=ch.url,
            )
            for ch in chapters
        ]

        # 下载正文
        fetched = self.engine.fetch_chapters(engine_chapters)

        # 组装书籍信息头
        author = getattr(self.engine, "_last_book_author", "") or ""
        category = getattr(self.engine, "_last_book_category", "") or ""
        status = getattr(self.engine, "_last_book_status", "") or ""

        # 已完结的书不爬取
        if status == "已完结":
            logger.info("状态已完结，跳过下载: %s", book.title)
            return os.path.join(os.getcwd(), f"Book_{book.id}.txt")  # dummy path, won't be used

        desc = book.description or ""

        header = f"书名：{book.title}\n"
        if author:
            header += f"作者：{author}\n"
        if category:
            header += f"分类：{category}\n"
        if status:
            header += f"状态：{status}\n"
        if desc:
            header += f"\n【作品简介】\n{desc}\n"
        header += "\n" + "=" * 40 + "\n\n"

        # 组装正文（含内容清洗）
        content_lines = [header]
        total = len(fetched)
        for i, ch in enumerate(fetched):
            text = ch.content or ""
            clean_title = re.sub(
                r'[（(][^）)]*?(?:求月票|求推荐|求收藏|求订阅|求追读|加更|儿童节|中秋节|国庆节|新年快乐|除夕快乐|感谢.*?盟)[^）)]*[）)]',
                '', ch.title
            ).strip()
            # 跳过所有非正文章节
            skip_patterns = [
                r'上架感言',
                r'感谢.*?(?:盟主|大佬|白银|黄金)',
                r'(?:属性|兵种|等级|属性\d)[\d.]*版',
                r'移动城市等级.*比例',
                r'^新书.*?(?:已上传|已开)',
                r'^请假条?$',
                r'^(?:病假|月休|休息|请假)',
                r'^(?:三江|强推|首页)感言',
                r'^重要通知',
                r'^单章说明',
                r'^情况说明',
                r'^更新说明',
                r'^书评.*活动',
                r'^有奖.*活动',
                r'^截止.*(?:属性|数据)',
                r'^月票.*(?:抽奖|加更|活动)',
                r'^\d+月月票',
                r'^月票.*(?:抽奖|加更|活动)',
                r'月份月票.*(?:抽奖|结果|活动|获奖|预告|公布)',
                r'月票礼物获奖',
                r'^\d+月月票',
                r'^送礼物',
                r'^加更计划',
                r'^更新时间调整',
                r'^配音挑战赛',
                r'^第一大卷总结',
                r'^\d{4}年.*你好',
                r'^神战，感谢大家',
                r'^关于近期争议',
                r'^出山的三个挑战',
                r'^卷末总结',
                r'^\d+月抽奖|^抽奖结果|^抽奖中奖名单',
                r'^中奖名单',
                r'宁拙早智',
                r'^求月票',
                r'月初加更预告|月票番外预告',
                r'加更预告.*求月票',
                r'月初投票抽奖',
                r'镇重道歉',
                r'碎碎念',
                r'^关于法莉雅',
                r'^额，没想到',
                r'没想到这就上架',
                r'^月末总结',
                r'^天王感言',
                r'^第一次拿到',
                r'^出车祸了',
                r'^汇报下情况',
                r'^以一刷之力',
                r'更名通知',
                r'^求票番外',
            ]
            if any(re.search(p, clean_title) for p in skip_patterns):
                continue
            content_lines.append(f"{clean_title}\n\n{text}")
            logger.info("下载进度: %d/%d (%.0f%%)", i + 1, total, (i + 1) / total * 100)

        path = os.path.join(os.getcwd(), filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n\n=\n\n".join(content_lines))

        logger.info("下载完成: %s (%d 章, %s)", path, total, output.upper())
        return path
