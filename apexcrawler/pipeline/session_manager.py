"""Per-domain session isolation: 同一域使用相同 engine + proxy + profile。

Session内禁止引擎切换以保证指纹一致性。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Session:
    """单个域名的会话状态，绑定 engine/proxy/tls_profile。"""

    domain: str
    engine: str = ""
    proxy: str = ""
    tls_profile: str = ""
    created_at: float = field(default_factory=time.monotonic)
    request_count: int = 0
    cookies: dict = field(default_factory=dict)

    def age(self) -> float:
        return time.monotonic() - self.created_at

    def increment(self) -> None:
        self.request_count += 1


class SessionManager:
    """按域名隔离会话管理器。

    确保同一域名始终使用相同的 engine、proxy 和 TLS profile，
    避免指纹不一致导致反爬检测触发。
    """

    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, domain: str) -> Session:
        """获取或创建域名对应的会话。"""
        if domain not in self._sessions:
            self._sessions[domain] = Session(domain=domain)
        return self._sessions[domain]

    def bind_engine(
        self,
        domain: str,
        engine: str,
        proxy: str = "",
        profile: str = "",
    ) -> Session:
        """绑定引擎和指纹配置到域名会话。"""
        s = self.get_or_create(domain)
        s.engine = engine
        s.proxy = proxy or s.proxy
        s.tls_profile = profile or s.tls_profile
        logger.info(
            f"Session bind: domain={domain} engine={engine} "
            f"proxy={bool(s.proxy)} profile={s.tls_profile}"
        )
        return s

    def is_engine_locked(self, domain: str, engine: str) -> bool:
        """检查域名是否已锁定到不同的引擎。

        如果已绑定其他引擎，返回 True 表示不应切换。
        """
        s = self._sessions.get(domain)
        if s and s.engine and s.engine != engine:
            logger.warning(
                f"Engine locked: domain={domain} bound_to={s.engine} "
                f"requested={engine}"
            )
            return True
        return False

    def ensure_consistency(self, domain: str, engine: str) -> bool:
        """检查引擎与会话一致性。首次使用时自动绑定。

        Returns True if engine is consistent with session.
        """
        s = self.get_or_create(domain)
        if not s.engine:
            s.engine = engine
            return True
        consistent = s.engine == engine
        if not consistent:
            logger.warning(
                f"Inconsistency: domain={domain} session_engine={s.engine} "
                f"requested={engine}"
            )
        return consistent

    def get_session(self, domain: str) -> Session | None:
        """获取域名会话，不存在则返回 None。"""
        return self._sessions.get(domain)

    def remove_session(self, domain: str) -> None:
        """移除域名会话。"""
        self._sessions.pop(domain, None)

    def cleanup_stale(self, max_age: float = 3600.0) -> int:
        """清理过期会话，返回清理数量。"""
        now = time.monotonic()
        stale = [
            d for d, s in self._sessions.items() if now - s.created_at > max_age
        ]
        for d in stale:
            self._sessions.pop(d)
        if stale:
            logger.info("Cleaned %s stale sessions", len(stale))
        return len(stale)
