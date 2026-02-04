"""Tests for LLM provider message/tool conversion functions."""

# pyright: reportPrivateUsage=false

from typing import Any

import pytest

from app.core.llm.providers.anthropic import AnthropicProvider
from app.core.llm.providers.openai import OpenAIProvider
from app.core.llm.types import Message, MessageRole, Tool, ToolCall, ToolResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def anthropic_provider() -> AnthropicProvider:
    return AnthropicProvider(api_key="test-key")


@pytest.fixture
def openai_provider() -> OpenAIProvider:
    return OpenAIProvider(api_key="test-key")


def _tool_fixture() -> Tool:
    return Tool(
        name="search",
        description="Search the web",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    )


# ===========================================================================
# Anthropic Provider — _convert_messages
# ===========================================================================


class TestAnthropicConvertMessages:
    def test_system_messages_skipped(self, anthropic_provider: AnthropicProvider) -> None:
        messages = [Message(role=MessageRole.SYSTEM, content="You are helpful.")]
        result = anthropic_provider._convert_messages(messages)
        assert result == []

    def test_user_message_string(self, anthropic_provider: AnthropicProvider) -> None:
        messages = [Message(role=MessageRole.USER, content="Hello")]
        result = anthropic_provider._convert_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == [{"type": "text", "text": "Hello"}]

    def test_user_message_list_content(self, anthropic_provider: AnthropicProvider) -> None:
        content_blocks: list[dict[str, Any]] = [{"type": "text", "text": "Hi"}, {"type": "text", "text": "there"}]
        messages = [Message(role=MessageRole.USER, content=content_blocks)]
        result = anthropic_provider._convert_messages(messages)
        assert result[0]["content"] == content_blocks

    def test_user_message_with_tool_results(self, anthropic_provider: AnthropicProvider) -> None:
        messages = [
            Message(
                role=MessageRole.USER,
                content="Here are the results",
                tool_results=[ToolResult(tool_call_id="tc1", content="result data")],
            )
        ]
        result = anthropic_provider._convert_messages(messages)
        content = result[0]["content"]
        assert len(content) == 2
        assert content[0] == {"type": "text", "text": "Here are the results"}
        assert content[1] == {"type": "tool_result", "tool_use_id": "tc1", "content": "result data"}

    def test_assistant_message_string(self, anthropic_provider: AnthropicProvider) -> None:
        messages = [Message(role=MessageRole.ASSISTANT, content="I can help.")]
        result = anthropic_provider._convert_messages(messages)
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == [{"type": "text", "text": "I can help."}]

    def test_assistant_message_empty_string(self, anthropic_provider: AnthropicProvider) -> None:
        messages = [Message(role=MessageRole.ASSISTANT, content="")]
        result = anthropic_provider._convert_messages(messages)
        # Empty string should not produce a text block
        assert result[0]["content"] == []

    def test_assistant_message_with_tool_calls(self, anthropic_provider: AnthropicProvider) -> None:
        tc = ToolCall(id="tc1", name="search", arguments={"query": "test"})
        messages = [Message(role=MessageRole.ASSISTANT, content="Let me search.", tool_calls=[tc])]
        result = anthropic_provider._convert_messages(messages)
        content = result[0]["content"]
        assert len(content) == 2
        assert content[0] == {"type": "text", "text": "Let me search."}
        assert content[1] == {"type": "tool_use", "id": "tc1", "name": "search", "input": {"query": "test"}}

    def test_tool_message(self, anthropic_provider: AnthropicProvider) -> None:
        messages = [
            Message(
                role=MessageRole.TOOL,
                content="",
                tool_results=[ToolResult(tool_call_id="tc1", content="search result")],
            )
        ]
        result = anthropic_provider._convert_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == [{"type": "tool_result", "tool_use_id": "tc1", "content": "search result"}]

    def test_tool_message_no_results_omitted(self, anthropic_provider: AnthropicProvider) -> None:
        messages = [Message(role=MessageRole.TOOL, content="")]
        result = anthropic_provider._convert_messages(messages)
        assert result == []

    def test_full_conversation(self, anthropic_provider: AnthropicProvider) -> None:
        tc = ToolCall(id="tc1", name="search", arguments={"q": "hello"})
        messages = [
            Message(role=MessageRole.SYSTEM, content="System prompt"),
            Message(role=MessageRole.USER, content="Search for hello"),
            Message(role=MessageRole.ASSISTANT, content="", tool_calls=[tc]),
            Message(
                role=MessageRole.TOOL,
                content="",
                tool_results=[ToolResult(tool_call_id="tc1", content="found it")],
            ),
            Message(role=MessageRole.ASSISTANT, content="Here is what I found."),
        ]
        result = anthropic_provider._convert_messages(messages)
        assert len(result) == 4  # system skipped
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "user"  # tool results as user
        assert result[3]["role"] == "assistant"


class TestAnthropicExtractSystemPrompt:
    def test_extracts_system(self, anthropic_provider: AnthropicProvider) -> None:
        messages = [
            Message(role=MessageRole.SYSTEM, content="Be helpful"),
            Message(role=MessageRole.USER, content="Hi"),
        ]
        assert anthropic_provider._extract_system_prompt(messages) == "Be helpful"

    def test_no_system(self, anthropic_provider: AnthropicProvider) -> None:
        messages = [Message(role=MessageRole.USER, content="Hi")]
        assert anthropic_provider._extract_system_prompt(messages) == ""


class TestAnthropicConvertTools:
    def test_converts_tools(self, anthropic_provider: AnthropicProvider) -> None:
        tool = _tool_fixture()
        result = anthropic_provider._convert_tools([tool])
        assert len(result) == 1
        assert result[0]["name"] == "search"
        assert result[0]["description"] == "Search the web"
        assert result[0]["input_schema"] == tool.input_schema


# ===========================================================================
# OpenAI Provider — _convert_messages
# ===========================================================================


class TestOpenAIConvertMessages:
    def test_system_message(self, openai_provider: OpenAIProvider) -> None:
        messages = [Message(role=MessageRole.SYSTEM, content="You are helpful.")]
        result = openai_provider._convert_messages(messages)
        assert result == [{"role": "system", "content": "You are helpful."}]

    def test_user_message(self, openai_provider: OpenAIProvider) -> None:
        messages = [Message(role=MessageRole.USER, content="Hello")]
        result = openai_provider._convert_messages(messages)
        assert result == [{"role": "user", "content": "Hello"}]

    def test_assistant_message(self, openai_provider: OpenAIProvider) -> None:
        messages = [Message(role=MessageRole.ASSISTANT, content="Hi there")]
        result = openai_provider._convert_messages(messages)
        assert result == [{"role": "assistant", "content": "Hi there"}]

    def test_assistant_message_with_tool_calls(self, openai_provider: OpenAIProvider) -> None:
        tc = ToolCall(id="tc1", name="search", arguments={"query": "test"})
        messages = [Message(role=MessageRole.ASSISTANT, content="Searching...", tool_calls=[tc])]
        result = openai_provider._convert_messages(messages)
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Searching..."
        assert len(result[0]["tool_calls"]) == 1
        assert result[0]["tool_calls"][0]["id"] == "tc1"
        assert result[0]["tool_calls"][0]["type"] == "function"
        assert result[0]["tool_calls"][0]["function"]["name"] == "search"
        assert result[0]["tool_calls"][0]["function"]["arguments"] == '{"query": "test"}'

    def test_tool_message(self, openai_provider: OpenAIProvider) -> None:
        messages = [
            Message(
                role=MessageRole.TOOL,
                content="",
                tool_results=[ToolResult(tool_call_id="tc1", content="search result")],
            )
        ]
        result = openai_provider._convert_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "tc1"
        assert result[0]["content"] == "search result"

    def test_tool_message_no_results(self, openai_provider: OpenAIProvider) -> None:
        messages = [Message(role=MessageRole.TOOL, content="")]
        result = openai_provider._convert_messages(messages)
        assert result == []

    def test_list_content_stringified(self, openai_provider: OpenAIProvider) -> None:
        content_blocks: list[dict[str, Any]] = [{"type": "text", "text": "Hello"}]
        messages = [Message(role=MessageRole.USER, content=content_blocks)]
        result = openai_provider._convert_messages(messages)
        # OpenAI provider stringifies non-string content
        assert result[0]["content"] == str(content_blocks)


class TestOpenAIConvertTools:
    def test_converts_tools(self, openai_provider: OpenAIProvider) -> None:
        tool = _tool_fixture()
        result = openai_provider._convert_tools([tool])
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "search"
        assert result[0]["function"]["description"] == "Search the web"
        assert result[0]["function"]["parameters"] == tool.input_schema


# ===========================================================================
# Edge cases — Anthropic
# ===========================================================================


class TestAnthropicEdgeCases:
    def test_assistant_list_content(self, anthropic_provider: AnthropicProvider) -> None:
        """Non-string content on assistant messages should be extended, not wrapped."""
        blocks: list[dict[str, Any]] = [{"type": "text", "text": "Hello"}]
        messages = [Message(role=MessageRole.ASSISTANT, content=blocks)]
        result = anthropic_provider._convert_messages(messages)
        assert result[0]["content"] == blocks

    def test_multiple_tool_results_in_tool_message(self, anthropic_provider: AnthropicProvider) -> None:
        """Multiple tool results in a single TOOL message should produce multiple blocks."""
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
        result = anthropic_provider._convert_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert len(result[0]["content"]) == 2
        assert result[0]["content"][0]["tool_use_id"] == "tc1"
        assert result[0]["content"][1]["tool_use_id"] == "tc2"

    def test_system_with_list_content(self, anthropic_provider: AnthropicProvider) -> None:
        """System message with non-string content should return empty string."""
        blocks: list[dict[str, Any]] = [{"type": "text", "text": "System"}]
        messages = [Message(role=MessageRole.SYSTEM, content=blocks)]
        assert anthropic_provider._extract_system_prompt(messages) == ""

    def test_multiple_tools(self, anthropic_provider: AnthropicProvider) -> None:
        """Multiple tools should all be converted."""
        tools = [
            Tool(name="search", description="Search", input_schema={"type": "object"}),
            Tool(name="launch", description="Launch", input_schema={"type": "object"}),
        ]
        result = anthropic_provider._convert_tools(tools)
        assert len(result) == 2
        assert result[0]["name"] == "search"
        assert result[1]["name"] == "launch"


# ===========================================================================
# Edge cases — OpenAI
# ===========================================================================


class TestOpenAIEdgeCases:
    def test_multiple_tool_results_expand(self, openai_provider: OpenAIProvider) -> None:
        """Multiple tool results should expand to multiple OpenAI tool messages."""
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
        result = openai_provider._convert_messages(messages)
        assert len(result) == 2
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "tc1"
        assert result[1]["role"] == "tool"
        assert result[1]["tool_call_id"] == "tc2"

    def test_assistant_no_tool_calls_has_no_key(self, openai_provider: OpenAIProvider) -> None:
        """Assistant message without tool_calls should not include the key."""
        messages = [Message(role=MessageRole.ASSISTANT, content="Just text")]
        result = openai_provider._convert_messages(messages)
        assert "tool_calls" not in result[0]

    def test_multiple_tools(self, openai_provider: OpenAIProvider) -> None:
        """Multiple tools should all be converted."""
        tools = [
            Tool(name="search", description="Search", input_schema={"type": "object"}),
            Tool(name="launch", description="Launch", input_schema={"type": "object"}),
        ]
        result = openai_provider._convert_tools(tools)
        assert len(result) == 2
        assert result[0]["function"]["name"] == "search"
        assert result[1]["function"]["name"] == "launch"

    def test_system_list_content_stringified(self, openai_provider: OpenAIProvider) -> None:
        """System message with list content should be stringified."""
        blocks: list[dict[str, Any]] = [{"type": "text", "text": "System"}]
        messages = [Message(role=MessageRole.SYSTEM, content=blocks)]
        result = openai_provider._convert_messages(messages)
        assert result[0]["content"] == str(blocks)
