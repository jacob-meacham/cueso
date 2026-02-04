"""Tests for the session store implementations."""

import time
from unittest.mock import MagicMock, patch

from app.core.llm.session_store import InMemorySessionStore


def _make_store(**kwargs: object) -> InMemorySessionStore:
    """Create a store with optional overrides."""
    return InMemorySessionStore(**kwargs)  # type: ignore[arg-type]


def _mock_provider() -> MagicMock:
    return MagicMock()


def _mock_config() -> MagicMock:
    cfg = MagicMock()
    cfg.system_prompt = "test"
    cfg.tools = []
    cfg.max_tokens = 1024
    cfg.max_iterations = 10
    cfg.temperature = 0.7
    cfg.stream = False
    cfg.pause_after = frozenset()
    return cfg


class TestCreateAndGet:
    def test_create_session(self) -> None:
        store = _make_store()
        session = store.create_session("s1", _mock_provider(), _mock_config())
        assert session is not None
        assert session.session_id == "s1"

    def test_get_session_returns_created(self) -> None:
        store = _make_store()
        store.create_session("s1", _mock_provider(), _mock_config())
        session = store.get_session("s1")
        assert session is not None
        assert session.session_id == "s1"

    def test_get_session_returns_none_for_unknown(self) -> None:
        store = _make_store()
        assert store.get_session("nonexistent") is None

    def test_create_overwrites_existing(self) -> None:
        store = _make_store()
        store.create_session("s1", _mock_provider(), _mock_config())
        store.create_session("s1", _mock_provider(), _mock_config())
        assert store.get_session("s1") is not None
        assert store.list_sessions() == ["s1"]


class TestDeleteAndList:
    def test_delete_session(self) -> None:
        store = _make_store()
        store.create_session("s1", _mock_provider(), _mock_config())
        store.delete_session("s1")
        assert store.get_session("s1") is None

    def test_delete_nonexistent_is_noop(self) -> None:
        store = _make_store()
        store.delete_session("nope")  # should not raise

    def test_list_sessions(self) -> None:
        store = _make_store()
        store.create_session("s1", _mock_provider(), _mock_config())
        store.create_session("s2", _mock_provider(), _mock_config())
        sessions = store.list_sessions()
        assert sorted(sessions) == ["s1", "s2"]

    def test_list_sessions_empty(self) -> None:
        store = _make_store()
        assert store.list_sessions() == []


class TestTTLExpiration:
    def test_expired_session_not_returned(self) -> None:
        store = _make_store(ttl_seconds=0.1)
        store.create_session("s1", _mock_provider(), _mock_config())
        time.sleep(0.15)
        assert store.get_session("s1") is None

    def test_expired_session_not_listed(self) -> None:
        store = _make_store(ttl_seconds=0.1)
        store.create_session("s1", _mock_provider(), _mock_config())
        time.sleep(0.15)
        assert store.list_sessions() == []

    def test_non_expired_session_survives(self) -> None:
        store = _make_store(ttl_seconds=10)
        store.create_session("s1", _mock_provider(), _mock_config())
        assert store.get_session("s1") is not None

    def test_get_session_refreshes_timestamp(self) -> None:
        """Accessing a session should update its LRU timestamp."""
        store = _make_store(ttl_seconds=0.3)
        store.create_session("s1", _mock_provider(), _mock_config())
        # Access at 0.15s — refreshes timestamp
        time.sleep(0.15)
        assert store.get_session("s1") is not None
        # Access again at 0.3s — only 0.15s since last touch
        time.sleep(0.15)
        assert store.get_session("s1") is not None

    def test_expired_evicted_on_create(self) -> None:
        """Creating a new session should evict expired ones."""
        store = _make_store(ttl_seconds=0.1)
        store.create_session("s1", _mock_provider(), _mock_config())
        time.sleep(0.15)
        store.create_session("s2", _mock_provider(), _mock_config())
        assert "s1" not in store.sessions
        assert "s2" in store.sessions


class TestMaxSessionsEviction:
    def test_evicts_oldest_when_at_capacity(self) -> None:
        store = _make_store(max_sessions=2)
        store.create_session("s1", _mock_provider(), _mock_config())
        store.create_session("s2", _mock_provider(), _mock_config())
        store.create_session("s3", _mock_provider(), _mock_config())
        # s1 should have been evicted (oldest)
        assert store.get_session("s1") is None
        assert store.get_session("s2") is not None
        assert store.get_session("s3") is not None

    def test_lru_evicts_least_recently_used(self) -> None:
        """Accessing a session should protect it from LRU eviction."""
        store = _make_store(max_sessions=2)
        store.create_session("s1", _mock_provider(), _mock_config())
        store.create_session("s2", _mock_provider(), _mock_config())
        # Touch s1 so s2 becomes LRU
        store.get_session("s1")
        store.create_session("s3", _mock_provider(), _mock_config())
        # s2 should be evicted (least recently used)
        assert store.get_session("s1") is not None
        assert store.get_session("s2") is None
        assert store.get_session("s3") is not None

    def test_max_sessions_one(self) -> None:
        store = _make_store(max_sessions=1)
        store.create_session("s1", _mock_provider(), _mock_config())
        store.create_session("s2", _mock_provider(), _mock_config())
        assert store.get_session("s1") is None
        assert store.get_session("s2") is not None

    def test_eviction_uses_monotonic_timestamps(self) -> None:
        """Verify we're using monotonic timestamps, not wall-clock time."""
        store = _make_store(max_sessions=2)
        # Each create_session calls monotonic() twice: once in _evict_expired, once for the timestamp
        with patch(
            "app.core.llm.session_store.time.monotonic",
            side_effect=[10.0, 10.0, 20.0, 20.0, 30.0, 30.0],
        ):
            store.create_session("s1", _mock_provider(), _mock_config())
            store.create_session("s2", _mock_provider(), _mock_config())
            store.create_session("s3", _mock_provider(), _mock_config())
        # s1 had timestamp 10.0, s2 had 20.0 — s1 evicted
        assert "s1" not in store.sessions
