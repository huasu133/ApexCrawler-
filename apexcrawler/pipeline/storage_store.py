"""Browser storage persistence — LocalStorage + IndexedDB capture and restore."""

import json, logging
logger = logging.getLogger(__name__)

class StorageStore:
    """Capture and restore browser-side storage for session continuity."""
    
    def __init__(self):
        self._local_storage: dict[str, dict] = {}
        self._session_storage: dict[str, dict] = {}
    
    async def capture(self, page, domain: str):
        """Capture LocalStorage and SessionStorage from a page."""
        try:
            ls = await page.evaluate("() => JSON.stringify(localStorage)")
            ss = await page.evaluate("() => JSON.stringify(sessionStorage)")
            self._local_storage[domain] = json.loads(ls or "{}")
            self._session_storage[domain] = json.loads(ss or "{}")
            logger.debug(f"Storage captured: {domain} (ls={len(self._local_storage[domain])}, ss={len(self._session_storage[domain])})")
        except Exception as e:
            logger.warning(f"Storage capture failed for {domain}: {e}")
    
    def generate_inject_script(self, domain: str) -> str:
        """Generate JS injection script to restore storage."""
        ls = self._local_storage.get(domain, {})
        ss = self._session_storage.get(domain, {})
        return f"""
// Auto-restore browser storage for {domain}
(function() {{
    try {{
        var ls = {json.dumps(ls)};
        var ss = {json.dumps(ss)};
        for (var k in ls) {{ localStorage.setItem(k, ls[k]); }}
        for (var k in ss) {{ sessionStorage.setItem(k, ss[k]); }}
    }} catch(e) {{}}
}})();
"""
    
    def clear(self, domain: str = ""):
        if domain:
            self._local_storage.pop(domain, None)
            self._session_storage.pop(domain, None)
        else:
            self._local_storage.clear()
            self._session_storage.clear()
