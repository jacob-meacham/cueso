"""In-memory session storage implementation."""

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
    """In-memory implementation of session storage."""

    def __init__(self):
        self.sessions: dict[str, LLMSession] = {}

    def create_session(
        self,
        session_id: str,
        provider: LLMProvider,
        config: SessionConfig,
    ) -> LLMSession:
        """Create a new LLM session."""

        session = LLMSession(session_id, provider, config)
        self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> LLMSession | None:
        """Get an existing session."""
        return self.sessions.get(session_id)

    def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        if session_id in self.sessions:
            del self.sessions[session_id]

    def list_sessions(self) -> list[str]:
        """List all active session IDs."""
        return list(self.sessions.keys())
