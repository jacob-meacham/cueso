"""Tests for LLM provider message/tool conversion functions."""

# pyright: reportPrivateUsage=false

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from app.api.chat import get_llm_provider
from app.core.config import settings
from app.core.llm.providers.openrouter import OpenRouterProvider
from app.core.llm.types import Message, MessageRole, SessionConfig, StreamResult, Tool, ToolCall, ToolResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def openrouter_provider() -> OpenRouterProvider:
    return OpenRouterProvider(api_key="test-key", model="test-model")


def _tool_fixture() -> Tool:
    return Tool(
        name="search",
        description="Search the web",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    )


# ===========================================================================
# OpenRouter Provider — _convert_messages
# ===========================================================================


class TestOpenRouterConvertMessages:
    def test_system_message(self, openrouter_provider: OpenRouterProvider) -> None:
        messages = [Message(role=MessageRole.SYSTEM, content="You are helpful.")]
        result = openrouter_provider._convert_messages(messages)
        assert result == [{"role": "system", "content": "You are helpful."}]

    def test_user_message(self, openrouter_provider: OpenRouterProvider) -> None:
        messages = [Message(role=MessageRole.USER, content="Hello")]
        result = openrouter_provider._convert_messages(messages)
        assert result == [{"role": "user", "content": "Hello"}]

    def test_assistant_message(self, openrouter_provider: OpenRouterProvider) -> None:
        messages = [Message(role=MessageRole.ASSISTANT, content="Hi there")]
        result = openrouter_provider._convert_messages(messages)
        assert result == [{"role": "assistant", "content": "Hi there"}]

    def test_assistant_message_with_tool_calls(self, openrouter_provider: OpenRouterProvider) -> None:
        tc = ToolCall(id="tc1", name="search", arguments={"query": "test"})
        messages = [Message(role=MessageRole.ASSISTANT, content="Searching...", tool_calls=[tc])]
        result = openrouter_provider._convert_messages(messages)
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Searching..."
        assert len(result[0]["tool_calls"]) == 1
        assert result[0]["tool_calls"][0]["id"] == "tc1"
        assert result[0]["tool_calls"][0]["type"] == "function"
        assert result[0]["tool_calls"][0]["function"]["name"] == "search"
        assert result[0]["tool_calls"][0]["function"]["arguments"] == '{"query": "test"}'

    def test_tool_message(self, openrouter_provider: OpenRouterProvider) -> None:
        messages = [
            Message(
                role=MessageRole.TOOL,
                content="",
                tool_results=[ToolResult(tool_call_id="tc1", content="search result")],
            )
        ]
        result = openrouter_provider._convert_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "tc1"
        assert result[0]["content"] == "search result"

    def test_tool_message_no_results(self, openrouter_provider: OpenRouterProvider) -> None:
        messages = [Message(role=MessageRole.TOOL, content="")]
        result = openrouter_provider._convert_messages(messages)
        assert result == []

    def test_list_content_stringified(self, openrouter_provider: OpenRouterProvider) -> None:
        content_blocks: list[dict[str, Any]] = [{"type": "text", "text": "Hello"}]
        messages = [Message(role=MessageRole.USER, content=content_blocks)]
        result = openrouter_provider._convert_messages(messages)
        # OpenRouter provider stringifies non-string content
        assert result[0]["content"] == str(content_blocks)


class TestOpenRouterConvertTools:
    def test_converts_tools(self, openrouter_provider: OpenRouterProvider) -> None:
        tool = _tool_fixture()
        result = openrouter_provider._convert_tools([tool])
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "search"
        assert result[0]["function"]["description"] == "Search the web"
        assert result[0]["function"]["parameters"] == tool.input_schema


# ===========================================================================
# Edge cases — OpenRouter
# ===========================================================================


class TestOpenRouterEdgeCases:
    def test_multiple_tool_results_expand(self, openrouter_provider: OpenRouterProvider) -> None:
        """Multiple tool results should expand to multiple OpenRouter tool messages."""
        messages = [
            Message(
                role=MessageRole.TOOL,
                content="",
                tool_results=[
                    ToolResult(tool_call_id="tc1", content="result 1"),
                    ToolResult(tool_call_id="tc2", content="result 2"),
                ],
            )
        ]
        result = openrouter_provider._convert_messages(messages)
        assert len(result) == 2
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "tc1"
        assert result[1]["role"] == "tool"
        assert result[1]["tool_call_id"] == "tc2"

    def test_assistant_no_tool_calls_has_no_key(self, openrouter_provider: OpenRouterProvider) -> None:
        """Assistant message without tool_calls should not include the key."""
        messages = [Message(role=MessageRole.ASSISTANT, content="Just text")]
        result = openrouter_provider._convert_messages(messages)
        assert "tool_calls" not in result[0]

    def test_multiple_tools(self, openrouter_provider: OpenRouterProvider) -> None:
        """Multiple tools should all be converted."""
        tools = [
            Tool(name="search", description="Search", input_schema={"type": "object"}),
            Tool(name="launch", description="Launch", input_schema={"type": "object"}),
        ]
        result = openrouter_provider._convert_tools(tools)
        assert len(result) == 2
        assert result[0]["function"]["name"] == "search"
        assert result[1]["function"]["name"] == "launch"

    def test_system_list_content_stringified(self, openrouter_provider: OpenRouterProvider) -> None:
        """System message with list content should be stringified."""
        blocks: list[dict[str, Any]] = [{"type": "text", "text": "System"}]
        messages = [Message(role=MessageRole.SYSTEM, content=blocks)]
        result = openrouter_provider._convert_messages(messages)
        assert result[0]["content"] == str(blocks)


# ===========================================================================
# OpenRouter Provider — client configuration
# ===========================================================================


class TestOpenRouterClientConfig:
    def test_default_base_url_and_attribution_header(self) -> None:
        provider = OpenRouterProvider(api_key="test-key", model="test-model")
        assert str(provider.client.base_url) == "https://openrouter.ai/api/v1/"
        assert provider.client.default_headers.get("X-Title") == "Cueso"
        assert provider.model == "test-model"

    def test_custom_base_url(self) -> None:
        provider = OpenRouterProvider(api_key="test-key", model="m", base_url="http://localhost:11434/v1")
        assert str(provider.client.base_url) == "http://localhost:11434/v1/"


# ===========================================================================
# OpenRouter Provider — streaming robustness
# ===========================================================================


def _chunk(choices: list[Any]) -> Any:
    return SimpleNamespace(choices=choices)


def _choice(content: str | None, finish_reason: str | None) -> Any:
    return SimpleNamespace(
        delta=SimpleNamespace(content=content, tool_calls=None),
        finish_reason=finish_reason,
    )


def _tool_call_delta(index: int, id: str | None = None, name: str | None = None, arguments: str | None = None) -> Any:
    return SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _choice_tool_calls(tool_calls: list[Any], finish_reason: str | None = None) -> Any:
    return SimpleNamespace(
        delta=SimpleNamespace(content=None, tool_calls=tool_calls),
        finish_reason=finish_reason,
    )


class TestOpenRouterStreamGuard:
    @pytest.mark.asyncio
    async def test_empty_choices_chunk_skipped(self, openrouter_provider: OpenRouterProvider) -> None:
        """OpenRouter emits keep-alive/usage frames with an empty choices array."""
        chunks = [
            _chunk([]),  # must be skipped, not IndexError
            _chunk([_choice("hi", None)]),
            _chunk([_choice(None, "stop")]),
        ]

        async def fake_stream() -> Any:
            for c in chunks:
                yield c

        result = StreamResult()
        config = SessionConfig(system_prompt="", tools=[])
        with patch.object(
            openrouter_provider.client.chat.completions,
            "create",
            AsyncMock(return_value=fake_stream()),
        ):
            events = [
                e
                async for e in openrouter_provider.generate_stream(
                    [Message(role=MessageRole.USER, content="hello")], config, result
                )
            ]

        assert result.content == "hi"
        assert any(e["type"] == "message_complete" for e in events)

    @pytest.mark.asyncio
    async def test_duplicate_finish_reason_chunks_finalize_tool_calls_once(
        self, openrouter_provider: OpenRouterProvider
    ) -> None:
        """Reproduces the observed OpenRouter Claude-route pattern: finish_reason
        is set on *two* separate chunks for the same turn (confirmed live against
        anthropic/claude-sonnet-4.5 via OpenRouter). The finalization block must
        not re-append the accumulated tool calls on the second occurrence, or the
        resulting tool_calls list contains duplicate ids — which 400s when the
        session loop resends history (`tool_use ids must be unique`).
        """
        chunks = [
            _chunk([_choice_tool_calls([_tool_call_delta(0, id="toolu_1", name="get_weather", arguments="")])]),
            _chunk([_choice_tool_calls([_tool_call_delta(0, arguments="")])]),
            _chunk([_choice_tool_calls([_tool_call_delta(0, arguments='{"city": "Seattle')])]),
            _chunk([_choice_tool_calls([_tool_call_delta(0, arguments='"}')])]),
            _chunk([_choice_tool_calls([_tool_call_delta(1, id="toolu_2", name="get_weather", arguments="")])]),
            _chunk([_choice_tool_calls([_tool_call_delta(1, arguments="")])]),
            _chunk([_choice_tool_calls([_tool_call_delta(1, arguments='{"city": "Boston')])]),
            _chunk([_choice_tool_calls([_tool_call_delta(1, arguments='"}')])]),
            _chunk([_choice("", "tool_calls")]),
            _chunk([_choice("", "tool_calls")]),
        ]

        async def fake_stream() -> Any:
            for c in chunks:
                yield c

        result = StreamResult()
        config = SessionConfig(system_prompt="", tools=[])
        with patch.object(
            openrouter_provider.client.chat.completions,
            "create",
            AsyncMock(return_value=fake_stream()),
        ):
            [
                e
                async for e in openrouter_provider.generate_stream(
                    [Message(role=MessageRole.USER, content="weather please")], config, result
                )
            ]

        assert len(result.tool_calls) == 2
        ids = [tc.id for tc in result.tool_calls]
        assert ids == ["toolu_1", "toolu_2"]
        assert len(set(ids)) == len(ids), f"duplicate tool_call ids: {ids}"


# ===========================================================================
# get_llm_provider dependency
# ===========================================================================


class TestGetLLMProvider:
    @pytest.mark.asyncio
    async def test_returns_openrouter_provider(self) -> None:
        with (
            patch.object(settings.llm, "provider", "openrouter"),
            patch.object(settings.llm, "api_key", SecretStr("test-key")),
            patch.object(settings.llm, "model", "anthropic/claude-sonnet-4.5"),
        ):
            provider = await get_llm_provider()
            assert isinstance(provider, OpenRouterProvider)
            assert provider.model == "anthropic/claude-sonnet-4.5"
            assert str(provider.client.base_url) == "https://openrouter.ai/api/v1/"

    @pytest.mark.asyncio
    async def test_unsupported_provider_raises(self) -> None:
        with (
            patch.object(settings.llm, "provider", "anthropic"),
            patch.object(settings.llm, "api_key", SecretStr("test-key")),
        ):
            with pytest.raises(ValueError, match="Unsupported LLM provider"):
                await get_llm_provider()

    @pytest.mark.asyncio
    async def test_missing_api_key_raises(self) -> None:
        with (
            patch.object(settings.llm, "provider", "openrouter"),
            patch.object(settings.llm, "api_key", None),
        ):
            with pytest.raises(ValueError, match="API key"):
                await get_llm_provider()
