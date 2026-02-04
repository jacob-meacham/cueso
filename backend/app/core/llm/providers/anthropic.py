"""Anthropic LLM provider implementation."""

from collections.abc import AsyncGenerator
from typing import Any, cast

import anthropic

from ..provider import LLMProvider
from ..types import Message, MessageRole, SessionConfig, Tool, ToolCall


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider implementation."""

    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022"):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert our Message format to Anthropic's format."""
        converted: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                # System messages are handled separately in Anthropic
                continue
            elif msg.role == MessageRole.USER:
                content: list[dict[str, Any]] = []
                if isinstance(msg.content, str):
                    content.append({"type": "text", "text": msg.content})
                else:
                    content.extend(msg.content)

                # Add tool results if present
                if msg.tool_results:
                    for tool_result in msg.tool_results:
                        content.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_result.tool_call_id,
                                "content": tool_result.content,
                            }
                        )

                converted.append({"role": "user", "content": content})
            elif msg.role == MessageRole.ASSISTANT:
                content: list[dict[str, Any]] = []
                if isinstance(msg.content, str) and msg.content:
                    content.append({"type": "text", "text": msg.content})
                elif not isinstance(msg.content, str):
                    content.extend(msg.content)

                # Add tool calls if present
                if msg.tool_calls:
                    for tool_call in msg.tool_calls:
                        content.append(
                            {
                                "type": "tool_use",
                                "id": tool_call.id,
                                "name": tool_call.name,
                                "input": tool_call.arguments,
                            }
                        )

                converted.append({"role": "assistant", "content": content})
            elif msg.role == MessageRole.TOOL:
                # Anthropic expects tool results as user messages
                content: list[dict[str, Any]] = []
                if msg.tool_results:
                    for tool_result in msg.tool_results:
                        content.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_result.tool_call_id,
                                "content": tool_result.content,
                            }
                        )
                if content:
                    converted.append({"role": "user", "content": content})

        return converted

    def _convert_tools(self, tools: list[Tool]) -> list[dict[str, Any]]:
        """Convert our Tool format to Anthropic's format."""
        return [
            {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema} for tool in tools
        ]

    def _parse_response(self, response: Any) -> tuple[str, list[ToolCall]]:
        """Parse Anthropic response into our format."""
        content = ""
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))

        return content, tool_calls

    async def generate(
        self,
        messages: list[Message],
        config: SessionConfig,
    ) -> tuple[str, list[ToolCall]]:
        """Generate a non-streaming response from Anthropic."""
        # Extract system prompt
        system_prompt = ""
        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                system_prompt = msg.content if isinstance(msg.content, str) else ""
                break

        # Convert messages and tools
        converted_messages = self._convert_messages(messages)
        converted_tools = self._convert_tools(config.tools) if config.tools else None

        # Make API call
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=config.max_tokens,
            system=system_prompt,
            messages=cast(Any, converted_messages),
            tools=cast(Any, converted_tools),
            temperature=config.temperature,
        )

        return self._parse_response(response)

    async def generate_stream(
        self,
        messages: list[Message],
        config: SessionConfig,
    ) -> AsyncGenerator[dict[str, Any]]:
        """Generate a streaming response from Anthropic."""
        # Extract system prompt
        system_prompt = ""
        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                system_prompt = msg.content if isinstance(msg.content, str) else ""
                break

        # Convert messages and tools
        converted_messages = self._convert_messages(messages)
        converted_tools = self._convert_tools(config.tools) if config.tools else None

        # Make streaming API call
        stream = await self.client.messages.create(
            model=self.model,
            max_tokens=config.max_tokens,
            system=system_prompt,
            messages=cast(Any, converted_messages),
            tools=cast(Any, converted_tools),
            temperature=config.temperature,
            stream=True,
        )

        current_content = ""
        current_tool_calls: list[ToolCall] = []
        # Track the tool block currently being streamed (id + name come from
        # content_block_start; partial JSON comes from content_block_delta).
        _active_tool_id: str = ""
        _active_tool_name: str = ""

        async for chunk in stream:
            if chunk.type == "content_block_start":
                # A new content block is starting.  For tool_use blocks this is
                # where the tool id and name are provided.
                block = chunk.content_block
                if block.type == "tool_use":
                    _active_tool_id = block.id
                    _active_tool_name = block.name
                    current_tool_calls.append(ToolCall(id=block.id, name=block.name, arguments={}))
                    yield {
                        "type": "tool_call_delta",
                        "tool_call": {"id": block.id, "name": block.name, "input": None},
                    }

            elif chunk.type == "content_block_delta":
                if chunk.delta.type == "text_delta":
                    current_content += chunk.delta.text
                    yield {"type": "content_delta", "content": chunk.delta.text, "role": "assistant"}

                elif chunk.delta.type == "input_json_delta":
                    # Partial JSON for the active tool's input — emit for UI
                    yield {
                        "type": "tool_call_delta",
                        "tool_call": {
                            "id": _active_tool_id,
                            "name": _active_tool_name,
                            "input_json": chunk.delta.partial_json,
                        },
                    }

            elif chunk.type == "message_delta":
                # Message is complete
                yield {
                    "type": "message_complete",
                    "content": current_content,
                    "tool_calls": [tc.name for tc in current_tool_calls],
                    "finish_reason": chunk.delta.stop_reason or "unknown",
                }
