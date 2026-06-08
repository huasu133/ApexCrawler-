"""Cookie jar persistence — Netscape format + Redis support."""
from __future__ import annotations

import os
import json
import time
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

class CookieJarStore:
    """Persist cookies across sessions for session continuity."""
    
    def __init__(self, storage_dir: str | None = None):
        self._dir = Path(storage_dir or Path.home() / ".apexcrawler" / "cookies")
        self._dir.mkdir(parents=True, exist_ok=True)
    
    def save(self, domain: str, cookies: list[dict]) -> str:
        """Save cookies for a domain. Returns file path."""
        filepath = self._dir / f"{domain.replace('.', '_')}.json"
        # Atomic write: write to temp file then rename
        fd, tmp_path = tempfile.mkstemp(dir=str(self._dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({
                    "domain": domain,
                    "saved_at": time.time(),
                    "cookies": cookies,
                }, f, indent=2)
            os.replace(tmp_path, filepath)
        except Exception:
            os.unlink(tmp_path)
            raise
        logger.debug("Cookies saved: %s (%s items)", domain, len(cookies))
        return str(filepath)
    
    def load(self, domain: str) -> list[dict] | None:
        """Load cookies for a domain."""
        filepath = self._dir / f"{domain.replace('.', '_')}.json"
        if not filepath.exists():
            return None
        with open(filepath) as f:
            data = json.load(f)
        age = time.time() - data.get("saved_at", 0)
        if age > 86400:  # Expire after 24h
            filepath.unlink()
            return None
        return data.get("cookies", [])
    
    def export_netscape(self, domain: str) -> str:
        """Export cookies in Netscape HTTP Cookie File format."""
        cookies = self.load(domain)
        if not cookies:
            return ""
        lines = ["# Netscape HTTP Cookie File"]
        for c in cookies:
            domain_flag = "TRUE" if c.get("domain", "").startswith(".") else "FALSE"
            secure = "TRUE" if c.get("secure") else "FALSE"
            expires = str(int(c.get("expires", time.time() + 3600)))
            path = c.get("path", "/")
            name = c.get("name", "")
            value = c.get("value", "")
            lines.append(f"{c.get('domain', '')}\t{domain_flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
        return "\n".join(lines)
    
    def delete(self, domain: str):
        filepath = self._dir / f"{domain.replace('.', '_')}.json"
        filepath.unlink(missing_ok=True)
