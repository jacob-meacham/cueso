"""Anthropic LLM provider implementation."""

import json
from collections.abc import AsyncGenerator
from typing import Any, cast

import anthropic

from ..provider import LLMProvider
from ..types import Message, MessageRole, SessionConfig, StreamResult, Tool, ToolCall


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider implementation."""

    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022"):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert our Message format to Anthropic's format.

        Anthropic handles system messages separately (via the `system` param),
        so they are skipped here. Tool results must be sent as user messages
        with type=tool_result blocks.
        """
        converted: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                continue
            elif msg.role == MessageRole.USER:
                content: list[dict[str, Any]] = []
                if isinstance(msg.content, str):
                    content.append({"type": "text", "text": msg.content})
                else:
                    content.extend(msg.content)

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

    def _extract_system_prompt(self, messages: list[Message]) -> str:
        """Extract the system prompt from messages."""
        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                return msg.content if isinstance(msg.content, str) else ""
        return ""

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
        system_prompt = self._extract_system_prompt(messages)
        converted_messages = self._convert_messages(messages)
        converted_tools = self._convert_tools(config.tools) if config.tools else None

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=config.max_tokens,
            system=system_prompt,
            messages=converted_messages,  # type: ignore[arg-type]
            tools=converted_tools,  # type: ignore[arg-type]
            temperature=config.temperature,
        )

        return self._parse_response(response)

    async def generate_stream(
        self,
        messages: list[Message],
        config: SessionConfig,
        result: StreamResult,
    ) -> AsyncGenerator[dict[str, Any]]:
        """Generate a streaming response from Anthropic.

        Yields event dicts for the client. Populates `result` with the
        accumulated content and tool_calls by the time the generator is done.
        """
        system_prompt = self._extract_system_prompt(messages)
        converted_messages = self._convert_messages(messages)
        converted_tools = self._convert_tools(config.tools) if config.tools else None

        stream = cast(
            Any,
            await self.client.messages.create(
                model=self.model,
                max_tokens=config.max_tokens,
                system=system_prompt,
                messages=converted_messages,  # type: ignore[arg-type]
                tools=converted_tools,  # type: ignore[arg-type]
                temperature=config.temperature,
                stream=True,
            ),
        )

        current_content = ""
        current_tool_calls: list[ToolCall] = []
        # Track partial JSON for the active tool's input arguments.
        _active_tool_id: str = ""
        _active_tool_name: str = ""
        _active_tool_json: str = ""

        async for chunk in stream:
            if chunk.type == "content_block_start":
                block = chunk.content_block
                if block.type == "tool_use":
                    _active_tool_id = block.id
                    _active_tool_name = block.name
                    _active_tool_json = ""
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
                    # Accumulate partial JSON so we can parse the full arguments later
                    _active_tool_json += chunk.delta.partial_json
                    yield {
                        "type": "tool_call_delta",
                        "tool_call": {
                            "id": _active_tool_id,
                            "name": _active_tool_name,
                            "input_json": chunk.delta.partial_json,
                        },
                    }

            elif chunk.type == "content_block_stop":
                # Finalize tool call arguments from accumulated JSON
                if _active_tool_json and current_tool_calls:
                    try:
                        current_tool_calls[-1].arguments = json.loads(_active_tool_json)
                    except json.JSONDecodeError:
                        current_tool_calls[-1].arguments = {"_raw": _active_tool_json}
                    _active_tool_json = ""

            elif chunk.type == "message_delta":
                yield {
                    "type": "message_complete",
                    "content": current_content,
                    "tool_calls": [tc.name for tc in current_tool_calls],
                    "finish_reason": chunk.delta.stop_reason or "unknown",
                }

        # Populate the result container for the session to consume
        result.content = current_content
        result.tool_calls = current_tool_calls
