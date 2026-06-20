#!/usr/bin/env python3
"""
zhuis1.com 小说下载器 v4.0 — ApexCrawler Novel适配器版

重构亮点：
  ✅ 作为ApexCrawler NovelEngine的适配器集成（adapter_zhuis1.py）
  ✅ 代码量减少70%（从577行→~60行薄包装）
  ✅ 自动享受ApexCrawler多引擎能力（CloakedEngine + 未来PyDoll备份）
  ✅ 保留v3全部优化：断点续传+智能延迟+分页处理+智能解析+日志
  ✅ 可通过NovelEngine统一调用：ne.download(url)
  ✅ 可通过MCP Server远程调用

用法：
  python3 download_zhuis1_v4.py                          # 下载全本
  python3 download_zhuis1_v4.py --start 1 --end 10       # 下载1-10章
  python3 download_zhuis1_v4.py --info                    # 仅查看章节列表
"""
import sys
import os
import argparse
import logging

# 添加ApexCrawler到路径
sys.path.insert(0, "/Users/songmoxin/WorkBuddy/2026-05-21-task-1")

from apexcrawler.novel import NovelEngine

# ============ 配置 ============
NOVEL_URL = "https://m.zhuis1.com/fs/23515217085/"
OUTPUT_DIR = "/Users/songmoxin/WorkBuddy/2026-06-07-15-08-52"
LOG_FILE = os.path.join(OUTPUT_DIR, "crawler_v4.log")

# ============ 日志 ============
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("Zhuis1V4")


def main():
    parser = argparse.ArgumentParser(description="zhuis1.com 小说下载器 v4.0")
    parser.add_argument("--url", default=NOVEL_URL, help="小说目录页URL")
    parser.add_argument("--start", type=int, default=1, help="起始章节（默认1）")
    parser.add_argument("--end", type=int, default=0, help="结束章节（默认0=全部）")
    parser.add_argument("--info", action="store_true", help="仅查看章节列表，不下载")
    parser.add_argument("--output", default="txt", help="输出格式（默认txt）")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("zhuis1.com 小说下载器 v4.0 (ApexCrawler Novel适配器)")
    logger.info("=" * 60)

    # 切换到输出目录（适配器会在cwd生成文件）
    os.chdir(OUTPUT_DIR)

    # 创建NovelEngine实例（自动加载所有适配器，包括zhuis1）
    ne = NovelEngine()

    if args.info:
        # 仅查看章节列表
        logger.info("📖 获取章节列表: %s", args.url)
        book = ne.info(args.url)
        logger.info("书名: %s", book.title)
        logger.info("章节数: %d", len(book.chapters))
        for ch in book.chapters[:10]:
            logger.info("  %d. %s", ch.index, ch.title)
        if len(book.chapters) > 10:
            logger.info("  ... (共%d章)", len(book.chapters))
        return

    # 下载小说
    logger.info("📥 开始下载: %s", args.url)
    if args.start > 1 or args.end > 0:
        logger.info("  章节范围: %d-%d", args.start, args.end if args.end > 0 else 9999)

    filepath = ne.download(args.url, start=args.start, end=args.end, output=args.output)
    logger.info("✅ 下载完成: %s", filepath)


if __name__ == "__main__":
    main()
