"""Tests for the WebSocket chat endpoint."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.llm.session_store import InMemorySessionStore


def _setup_app() -> tuple[TestClient, MagicMock, MagicMock]:
    """Set up a TestClient with mocked LLM provider and tool executor.

    Returns (client, mock_provider, mock_executor).
    """
    from app.api.chat import get_llm_provider, get_tool_executor
    from main import app

    app.state.http_client = httpx.AsyncClient()
    app.state.session_store = InMemorySessionStore(max_sessions=10, ttl_seconds=3600)

    mock_provider = MagicMock()
    mock_provider.generate = AsyncMock(return_value=("Hello!", []))

    mock_executor = MagicMock()
    mock_executor.execute_tool = AsyncMock(return_value="result")

    app.dependency_overrides[get_llm_provider] = lambda: mock_provider
    app.dependency_overrides[get_tool_executor] = lambda: mock_executor

    return TestClient(app), mock_provider, mock_executor


def _teardown_app() -> None:
    from main import app

    app.dependency_overrides.clear()


def _drain_until_final(ws: object) -> list[dict[str, object]]:
    """Receive WebSocket messages until a 'final' event is found."""
    events: list[dict[str, object]] = []
    while True:
        event: dict[str, object] = json.loads(ws.receive_text())  # type: ignore[union-attr]
        events.append(event)
        if event["type"] == "final":
            break
    return events


class TestWebSocketConnect:
    def test_connect_and_send_message(self) -> None:
        """Test basic WebSocket connection and message send/receive."""
        client, _, _ = _setup_app()
        try:
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_text(json.dumps({"message": "Hello", "session_id": None}))

                # First response: session_created
                data: dict[str, object] = json.loads(ws.receive_text())
                assert data["type"] == "session_created"
                assert "session_id" in data
                session_id = data["session_id"]
                assert isinstance(session_id, str)
                assert len(session_id) > 0

                events = _drain_until_final(ws)
                final = events[-1]
                assert final["type"] == "final"
                assert final["session_id"] == session_id
        finally:
            _teardown_app()

    def test_session_reuse(self) -> None:
        """Test that sending a session_id reuses the same session."""
        client, _, _ = _setup_app()
        try:
            with client.websocket_connect("/ws/chat") as ws:
                # First message — get a session_id
                ws.send_text(json.dumps({"message": "First", "session_id": None}))
                data: dict[str, object] = json.loads(ws.receive_text())
                assert data["type"] == "session_created"
                session_id = data["session_id"]

                _drain_until_final(ws)

                # Second message — reuse the session_id
                ws.send_text(json.dumps({"message": "Second", "session_id": session_id}))
                data2: dict[str, object] = json.loads(ws.receive_text())
                assert data2["type"] == "session_created"
                assert data2["session_id"] == session_id

                _drain_until_final(ws)
        finally:
            _teardown_app()

    def test_malformed_json_sends_error(self) -> None:
        """Test that sending malformed JSON produces an error event."""
        client, _, _ = _setup_app()
        try:
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_text("not valid json {{{")
                data: dict[str, object] = json.loads(ws.receive_text())
                assert data["type"] == "error"
        finally:
            _teardown_app()


class TestOriginValidation:
    def test_allowed_origin_connects(self) -> None:
        """Test that a request with an allowed origin can connect."""
        client, _, _ = _setup_app()
        with patch("app.api.chat.settings.app.allowed_origins", ["http://localhost:3000"]):
            try:
                with client.websocket_connect("/ws/chat", headers={"origin": "http://localhost:3000"}) as ws:
                    ws.send_text(json.dumps({"message": "Hello"}))
                    data: dict[str, object] = json.loads(ws.receive_text())
                    assert data["type"] == "session_created"
                    _drain_until_final(ws)
            finally:
                _teardown_app()

    def test_disallowed_origin_rejected(self) -> None:
        """Test that a request with a disallowed origin is rejected."""
        client, _, _ = _setup_app()
        with patch("app.api.chat.settings.app.allowed_origins", ["http://localhost:3000"]):
            try:
                with pytest.raises(WebSocketDisconnect):
                    with client.websocket_connect("/ws/chat", headers={"origin": "http://evil.example.com"}) as ws:
                        ws.send_text(json.dumps({"message": "Hello"}))
            finally:
                _teardown_app()

    def test_empty_allowed_origins_allows_all(self) -> None:
        """Test that empty allowed_origins allows any origin."""
        client, _, _ = _setup_app()
        with patch("app.api.chat.settings.app.allowed_origins", []):
            try:
                with client.websocket_connect("/ws/chat", headers={"origin": "http://any-origin.example.com"}) as ws:
                    ws.send_text(json.dumps({"message": "Hello"}))
                    data: dict[str, object] = json.loads(ws.receive_text())
                    assert data["type"] == "session_created"
                    _drain_until_final(ws)
            finally:
                _teardown_app()
