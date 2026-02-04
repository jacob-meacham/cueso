"""In-memory session storage implementation with TTL and eviction."""

import time
from abc import ABC, abstractmethod

from .provider import LLMProvider
from .session import LLMSession
from .types import SessionConfig


class SessionStore(ABC):
    """Abstract base class for session storage."""

    @abstractmethod
    def create_session(
        self,
        session_id: str,
        provider: LLMProvider,
        config: SessionConfig,
    ) -> LLMSession:
        """Create a new LLM session."""
        pass

    @abstractmethod
    def get_session(self, session_id: str) -> LLMSession | None:
        """Get an existing session."""
        pass

    @abstractmethod
    def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        pass

    @abstractmethod
    def list_sessions(self) -> list[str]:
        """List all active session IDs."""
        pass


class InMemorySessionStore(SessionStore):
    """In-memory session storage with TTL expiration and LRU eviction."""

    def __init__(self, max_sessions: int = 100, ttl_seconds: float = 3600) -> None:
        self.sessions: dict[str, LLMSession] = {}
        self._timestamps: dict[str, float] = {}
        self._max_sessions = max_sessions
        self._ttl_seconds = ttl_seconds

    def _evict_expired(self) -> None:
        """Remove sessions that have exceeded their TTL."""
        now = time.monotonic()
        expired = [sid for sid, ts in self._timestamps.items() if now - ts > self._ttl_seconds]
        for sid in expired:
            del self.sessions[sid]
            del self._timestamps[sid]

    def _evict_lru(self) -> None:
        """Remove the least-recently-used session if at capacity."""
        if len(self.sessions) >= self._max_sessions:
            oldest = min(self._timestamps, key=lambda k: self._timestamps[k])
            del self.sessions[oldest]
            del self._timestamps[oldest]

    def create_session(
        self,
        session_id: str,
        provider: LLMProvider,
        config: SessionConfig,
    ) -> LLMSession:
        """Create a new LLM session, evicting expired/old sessions as needed."""
        self._evict_expired()
        self._evict_lru()
        session = LLMSession(session_id, provider, config)
        self.sessions[session_id] = session
        self._timestamps[session_id] = time.monotonic()
        return session

    def get_session(self, session_id: str) -> LLMSession | None:
        """Get an existing session, touching its timestamp for LRU tracking."""
        self._evict_expired()
        session = self.sessions.get(session_id)
        if session is not None:
            self._timestamps[session_id] = time.monotonic()
        return session

    def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        self.sessions.pop(session_id, None)
        self._timestamps.pop(session_id, None)

    def list_sessions(self) -> list[str]:
        """List all active (non-expired) session IDs."""
        self._evict_expired()
        return list(self.sessions.keys())
