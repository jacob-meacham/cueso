"""Tests for LLMSession conversation management."""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.llm.provider import LLMProvider
from app.core.llm.session import LLMSession
from app.core.llm.types import (
    Message,
    MessageRole,
    SessionConfig,
    StreamResult,
    Tool,
    ToolCall,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DUMMY_TOOLS = [Tool(name="search", description="Search", input_schema={"type": "object"})]


def _make_config(*, stream: bool = False, pause_after: frozenset[str] | None = None) -> SessionConfig:
    return SessionConfig(
        system_prompt="You are a test assistant.",
        tools=DUMMY_TOOLS,
        max_tokens=100,
        max_iterations=5,
        temperature=0.0,
        stream=stream,
        pause_after=pause_after or frozenset(),
    )


def _make_mock_provider() -> AsyncMock:
    """Create a mock LLMProvider with spec."""
    return AsyncMock(spec=LLMProvider)


async def _collect_events(gen: AsyncGenerator[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exhaust an async generator and return all yielded items."""
    events: list[dict[str, Any]] = []
    async for event in gen:
        events.append(event)
    return events


# ===========================================================================
# Non-streaming tests
# ===========================================================================


class TestSessionNonStreaming:
    @pytest.mark.asyncio
    async def test_simple_response(self) -> None:
        provider = _make_mock_provider()
        provider.generate.return_value = ("Hello!", [])

        session = LLMSession("s1", provider, _make_config(stream=False))
        events = await _collect_events(session.chat("Hi"))

        # Should have a final event
        assert any(e["type"] == "final" for e in events)
        final = next(e for e in events if e["type"] == "final")
        assert final["content"] == "Hello!"
        assert final["tool_calls"] == []
        assert final["session_id"] == "s1"

    @pytest.mark.asyncio
    async def test_messages_tracked(self) -> None:
        provider = _make_mock_provider()
        provider.generate.return_value = ("Response", [])

        session = LLMSession("s1", provider, _make_config(stream=False))
        await _collect_events(session.chat("Hi"))

        # Should have system + user + assistant = 3 messages
        assert len(session.messages) == 3
        assert session.messages[0].role == MessageRole.SYSTEM
        assert session.messages[1].role == MessageRole.USER
        assert session.messages[2].role == MessageRole.ASSISTANT


# ===========================================================================
# Tool calling tests
# ===========================================================================


class TestSessionToolCalls:
    @pytest.mark.asyncio
    async def test_tool_call_and_requery(self) -> None:
        provider = _make_mock_provider()
        tc = ToolCall(id="tc1", name="search", arguments={"query": "test"})

        # First call returns a tool call, second returns final content
        provider.generate.side_effect = [
            ("", [tc]),
            ("Found the answer.", []),
        ]

        tool_executor = AsyncMock(return_value="search result data")

        session = LLMSession("s1", provider, _make_config(stream=False))
        events = await _collect_events(session.chat("Search for test", tool_executor=tool_executor))

        # Tool executor should have been called
        tool_executor.assert_called_once_with(tc)

        # Should have tool_result and final events
        tool_results = [e for e in events if e["type"] == "tool_result"]
        assert len(tool_results) == 1
        assert tool_results[0]["result"] == "search result data"

        final = next(e for e in events if e["type"] == "final")
        assert final["content"] == "Found the answer."

    @pytest.mark.asyncio
    async def test_tool_executor_error_handled(self) -> None:
        provider = _make_mock_provider()
        tc = ToolCall(id="tc1", name="search", arguments={"query": "fail"})

        provider.generate.side_effect = [
            ("", [tc]),
            ("Sorry, I couldn't search.", []),
        ]

        tool_executor = AsyncMock(side_effect=RuntimeError("Network error"))

        session = LLMSession("s1", provider, _make_config(stream=False))
        events = await _collect_events(session.chat("Search", tool_executor=tool_executor))

        tool_results = [e for e in events if e["type"] == "tool_result"]
        assert len(tool_results) == 1
        assert tool_results[0]["error"] is True
        assert "Network error" in tool_results[0]["result"]

    @pytest.mark.asyncio
    async def test_no_tool_executor_breaks_loop(self) -> None:
        provider = _make_mock_provider()
        tc = ToolCall(id="tc1", name="search", arguments={"query": "test"})
        provider.generate.return_value = ("", [tc])

        session = LLMSession("s1", provider, _make_config(stream=False))
        events = await _collect_events(session.chat("Search"))

        # Without a tool executor, the loop should break after first LLM call
        assert provider.generate.call_count == 1
        final = next(e for e in events if e["type"] == "final")
        assert final["tool_calls"] == ["search"]


# ===========================================================================
# Streaming tests
# ===========================================================================


class TestSessionStreaming:
    @pytest.mark.asyncio
    async def test_streaming_populates_result(self) -> None:
        provider = _make_mock_provider()

        # Mock generate_stream as an async generator that populates StreamResult
        async def mock_stream(
            messages: list[Message], config: SessionConfig, result: StreamResult
        ) -> AsyncGenerator[dict[str, Any]]:
            yield {"type": "content_delta", "content": "Hello", "role": "assistant"}
            yield {"type": "content_delta", "content": " world", "role": "assistant"}
            yield {"type": "message_complete", "content": "Hello world", "tool_calls": [], "finish_reason": "end_turn"}
            result.content = "Hello world"
            result.tool_calls = []

        provider.generate_stream = mock_stream

        session = LLMSession("s1", provider, _make_config(stream=True))
        events = await _collect_events(session.chat("Hi"))

        content_deltas = [e for e in events if e["type"] == "content_delta"]
        assert len(content_deltas) == 2
        assert content_deltas[0]["content"] == "Hello"
        assert content_deltas[1]["content"] == " world"

        final = next(e for e in events if e["type"] == "final")
        assert final["content"] == "Hello world"

    @pytest.mark.asyncio
    async def test_streaming_with_tool_calls(self) -> None:
        provider = _make_mock_provider()
        tc = ToolCall(id="tc1", name="search", arguments={"query": "test"})

        # First streaming call returns a tool call
        async def mock_stream_with_tool(
            messages: list[Message], config: SessionConfig, result: StreamResult
        ) -> AsyncGenerator[dict[str, Any]]:
            yield {"type": "tool_call_delta", "tool_call": {"id": "tc1", "name": "search", "input": None}}
            yield {"type": "message_complete", "content": "", "tool_calls": ["search"], "finish_reason": "tool_use"}
            result.content = ""
            result.tool_calls = [tc]

        # Second call (non-streaming generate for requery) returns final response
        async def mock_stream_final(
            messages: list[Message], config: SessionConfig, result: StreamResult
        ) -> AsyncGenerator[dict[str, Any]]:
            yield {"type": "content_delta", "content": "Found it!", "role": "assistant"}
            yield {"type": "message_complete", "content": "Found it!", "tool_calls": [], "finish_reason": "end_turn"}
            result.content = "Found it!"
            result.tool_calls = []

        provider.generate_stream = AsyncMock(side_effect=[mock_stream_with_tool, mock_stream_final])  # type: ignore[method-assign]
        # We need generate_stream to be callable and return the async generators
        # Using side_effect with the generator functions directly
        call_count = 0

        async def side_effect_stream(
            messages: list[Message], config: SessionConfig, result: StreamResult
        ) -> AsyncGenerator[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                async for event in mock_stream_with_tool(messages, config, result):
                    yield event
            else:
                async for event in mock_stream_final(messages, config, result):
                    yield event

        provider.generate_stream = side_effect_stream

        tool_executor = AsyncMock(return_value="search result")

        session = LLMSession("s1", provider, _make_config(stream=True))
        events = await _collect_events(session.chat("Search for test", tool_executor=tool_executor))

        tool_executor.assert_called_once_with(tc)
        final = next(e for e in events if e["type"] == "final")
        assert final["content"] == "Found it!"


# ===========================================================================
# Max iterations
# ===========================================================================


class TestSessionMaxIterations:
    @pytest.mark.asyncio
    async def test_stops_at_max_iterations(self) -> None:
        provider = _make_mock_provider()
        # Always return a tool call so the loop continues
        tc = ToolCall(id="tc1", name="search", arguments={"query": "loop"})
        provider.generate.return_value = ("", [tc])

        tool_executor = AsyncMock(return_value="result")

        config = _make_config(stream=False)
        # max_iterations = 5
        session = LLMSession("s1", provider, config)
        events = await _collect_events(session.chat("Loop forever", tool_executor=tool_executor))

        final = next(e for e in events if e["type"] == "final")
        assert final["iteration_count"] == 5
        # generate should have been called max_iterations times
        assert provider.generate.call_count == 5


# ===========================================================================
# Pause after
# ===========================================================================


class TestSessionPauseAfter:
    @pytest.mark.asyncio
    async def test_pauses_after_specified_tool(self) -> None:
        provider = _make_mock_provider()
        tc = ToolCall(id="tc1", name="launch_on_roku", arguments={"channel_id": "12"})
        provider.generate.return_value = ("", [tc])

        tool_executor = AsyncMock(return_value="launched")

        config = _make_config(stream=False, pause_after=frozenset({"launch_on_roku"}))
        session = LLMSession("s1", provider, config)
        events = await _collect_events(session.chat("Launch Netflix", tool_executor=tool_executor))

        final = next(e for e in events if e["type"] == "final")
        assert final["paused"] is True
        # Should only have called generate once (paused after tool execution)
        assert provider.generate.call_count == 1


# ===========================================================================
# Reset
# ===========================================================================


class TestSessionReset:
    def test_reset_preserves_system_message(self) -> None:
        provider = _make_mock_provider()
        session = LLMSession("s1", provider, _make_config())

        # Add some messages manually
        session.add_message(Message(role=MessageRole.USER, content="Hello"))
        session.add_message(Message(role=MessageRole.ASSISTANT, content="Hi"))
        assert len(session.messages) == 3  # system + user + assistant

        session.reset()

        assert len(session.messages) == 1
        assert session.messages[0].role == MessageRole.SYSTEM
        assert session.iteration_count == 0

    def test_get_system_prompt(self) -> None:
        provider = _make_mock_provider()
        session = LLMSession("s1", provider, _make_config())
        assert session.get_system_prompt() == "You are a test assistant."

    def test_get_system_prompt_empty(self) -> None:
        provider = _make_mock_provider()
        config = _make_config()
        config.system_prompt = ""
        session = LLMSession("s1", provider, config)
        assert session.get_system_prompt() == ""


# ===========================================================================
# Edge cases
# ===========================================================================


class TestSessionEdgeCases:
    @pytest.mark.asyncio
    async def test_multiple_tool_calls_in_single_response(self) -> None:
        """When the LLM returns multiple tool calls, all should be executed."""
        provider = _make_mock_provider()
        tc1 = ToolCall(id="tc1", name="search", arguments={"query": "a"})
        tc2 = ToolCall(id="tc2", name="search", arguments={"query": "b"})

        provider.generate.side_effect = [
            ("", [tc1, tc2]),
            ("Both done.", []),
        ]

        tool_executor = AsyncMock(side_effect=["result-a", "result-b"])

        session = LLMSession("s1", provider, _make_config(stream=False))
        events = await _collect_events(session.chat("Search both", tool_executor=tool_executor))

        tool_results = [e for e in events if e["type"] == "tool_result"]
        assert len(tool_results) == 2
        assert tool_results[0]["result"] == "result-a"
        assert tool_results[1]["result"] == "result-b"

        final = next(e for e in events if e["type"] == "final")
        assert final["content"] == "Both done."

    @pytest.mark.asyncio
    async def test_iteration_count_persists_across_chats(self) -> None:
        """iteration_count should persist across multiple chat() calls on the same session."""
        provider = _make_mock_provider()
        provider.generate.return_value = ("Reply", [])

        session = LLMSession("s1", provider, _make_config(stream=False))

        events1 = await _collect_events(session.chat("First"))
        final1 = next(e for e in events1 if e["type"] == "final")
        assert final1["iteration_count"] == 1

        events2 = await _collect_events(session.chat("Second"))
        final2 = next(e for e in events2 if e["type"] == "final")
        assert final2["iteration_count"] == 2

    @pytest.mark.asyncio
    async def test_no_system_prompt(self) -> None:
        """Session with empty system prompt should work and have no system message."""
        provider = _make_mock_provider()
        provider.generate.return_value = ("Hello", [])

        config = _make_config(stream=False)
        config.system_prompt = ""
        session = LLMSession("s1", provider, config)

        # No system message should be added
        assert len(session.messages) == 0

        events = await _collect_events(session.chat("Hi"))
        assert any(e["type"] == "final" for e in events)
        # user + assistant = 2 messages
        assert len(session.messages) == 2
