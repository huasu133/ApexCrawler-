"""起点中文网 API 封装 — 章节列表获取与缓存。"""

import json
import time
import hashlib
from typing import Optional
from apexcrawler.http.stealth_client import StealthHTTPClient


class CatalogFetcher:
    """起点中文网章节列表获取器。"""
    
    CATALOG_API = "https://book.qidian.com/ajax/book/category"
    BOOK_INFO_URL = "https://book.qidian.com/info/{book_id}"
    
    def __init__(self, cookies: Optional[list] = None, cache_ttl: int = 86400):
        self.client = StealthHTTPClient()
        self.cache_ttl = cache_ttl
        self._cache = {}
        if cookies:
            for c in cookies:
                self.client.session.cookies.set(c.get("name"), c.get("value"))
    
    def get_free_chapters(self, book_id: str) -> dict:
        """获取免费章节列表。返回带章节数据的字典。"""
        cache_key = f"catalog_{book_id}"
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached["ts"] < self.cache_ttl:
            return cached["data"]
        
        data = self._fetch_catalog(book_id)
        if not data:
            return {"total": 0, "free_count": 0, "vip_count": 0, "chapters": []}
        
        chapters = []
        free_count = 0
        vip_count = 0
        
        for volume in data.get("vs", []):
            for ch in volume.get("cs", []):
                is_free = ch.get("sS", 1) == 0
                chapter = {
                    "title": ch.get("cName", ""),
                    "cU": ch.get("cU", ""),
                    "index": ch.get("cnt", 0),
                    "is_free": is_free,
                    "vip_status": ch.get("sS", 1),
                    "update_time": ch.get("uT", ""),
                    "words_count": ch.get("cnt", 0),
                }
                chapters.append(chapter)
                if is_free:
                    free_count += 1
                else:
                    vip_count += 1
        
        result = {
            "total": len(chapters),
            "free_count": free_count,
            "vip_count": vip_count,
            "chapters": chapters,
        }
        self._cache[cache_key] = {"ts": time.time(), "data": result}
        return result
    
    def _fetch_catalog(self, book_id: str) -> Optional[dict]:
        """请求起点章节列表 API。"""
        url = f"{self.CATALOG_API}?bookId={book_id}"
        try:
            resp = self.client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    return data.get("data", {})
        except Exception as e:
            print(f"Catalog fetch error: {e}")
        return None
