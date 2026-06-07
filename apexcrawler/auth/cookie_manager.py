"""Cookie 安全存储与管理。"""

import json
import os
from pathlib import Path
from cryptography.fernet import Fernet


class CookieManager:
    """Cookie 加解密存储管理器。"""
    
    def __init__(self, key: bytes = None, cookie_dir: str = None):
        if key:
            self.cipher = Fernet(key)
        else:
            self.cipher = Fernet(Fernet.generate_key())
        self.cookie_dir = Path(cookie_dir or "./cookies")
        self.cookie_dir.mkdir(parents=True, exist_ok=True)
    
    def save(self, cookies: list[dict], domain: str = "qidian"):
        """加密保存 Cookie 到文件。"""
        path = self.cookie_dir / f"{domain}.enc"
        data = json.dumps(cookies, ensure_ascii=False).encode()
        encrypted = self.cipher.encrypt(data)
        path.write_bytes(encrypted)
    
    def load(self, domain: str = "qidian") -> list[dict]:
        """解密加载 Cookie。"""
        path = self.cookie_dir / f"{domain}.enc"
        if not path.exists():
            return []
        encrypted = path.read_bytes()
        decrypted = self.cipher.decrypt(encrypted)
        return json.loads(decrypted.decode())
    
    def exists(self, domain: str = "qidian") -> bool:
        return (self.cookie_dir / f"{domain}.enc").exists()
