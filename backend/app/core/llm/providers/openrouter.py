"""OpenRouter LLM provider implementation (OpenAI-compatible gateway)."""

import json
from collections.abc import AsyncGenerator
from typing import Any, cast

import openai

from ..provider import LLMProvider
from ..types import Message, MessageRole, SessionConfig, StreamResult, Tool, ToolCall

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider(LLMProvider):
    """OpenRouter provider — routes to any model behind OpenRouter's OpenAI-compatible API."""

    def __init__(self, api_key: str, model: str, base_url: str = OPENROUTER_BASE_URL):
        self.client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers={"X-Title": "Cueso"},
        )
        self.model = model

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert our Message format to OpenAI-compatible format.

        OpenAI-compatible expects tool results as role=tool messages with tool_call_id,
        and assistant messages with tool_calls must include the tool_calls array.
        """
        converted: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                converted.append(
                    {"role": "system", "content": msg.content if isinstance(msg.content, str) else str(msg.content)}
                )
            elif msg.role == MessageRole.USER:
                converted.append(
                    {"role": "user", "content": msg.content if isinstance(msg.content, str) else str(msg.content)}
                )
            elif msg.role == MessageRole.ASSISTANT:
                entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": msg.content if isinstance(msg.content, str) else str(msg.content),
                }
                if msg.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                        }
                        for tc in msg.tool_calls
                    ]
                converted.append(entry)
            elif msg.role == MessageRole.TOOL:
                if msg.tool_results:
                    for tool_result in msg.tool_results:
                        converted.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_result.tool_call_id,
                                "content": tool_result.content,
                            }
                        )

        return converted

    def _convert_tools(self, tools: list[Tool]) -> list[dict[str, Any]]:
        """Convert our Tool format to OpenAI-compatible function calling format."""
        return [
            {
                "type": "function",
                "function": {"name": tool.name, "description": tool.description, "parameters": tool.input_schema},
            }
            for tool in tools
        ]

    def _parse_response(self, response: Any) -> tuple[str, list[ToolCall]]:
        """Parse OpenAI response into our format."""
        content = response.choices[0].message.content or ""
        tool_calls: list[ToolCall] = []

        if response.choices[0].message.tool_calls:
            for tool_call in response.choices[0].message.tool_calls:
                try:
                    arguments = json.loads(tool_call.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    arguments = {"_raw": tool_call.function.arguments}
                tool_calls.append(ToolCall(id=tool_call.id, name=tool_call.function.name, arguments=arguments))

        return content, tool_calls

    async def generate(
        self,
        messages: list[Message],
        config: SessionConfig,
    ) -> tuple[str, list[ToolCall]]:
        """Generate a non-streaming response from OpenRouter."""
        converted_messages = self._convert_messages(messages)
        converted_tools = self._convert_tools(config.tools) if config.tools else None

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": converted_messages,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
        }
        if converted_tools:
            kwargs["tools"] = converted_tools
            kwargs["tool_choice"] = "auto"

        response = await self.client.chat.completions.create(**kwargs)  # type: ignore[arg-type]

        return self._parse_response(response)

    async def generate_stream(
        self,
        messages: list[Message],
        config: SessionConfig,
        result: StreamResult,
    ) -> AsyncGenerator[dict[str, Any]]:
        """Generate a streaming response from OpenRouter.

        Yields event dicts for the client. Populates `result` with the
        accumulated content and tool_calls by the time the generator is done.
        """
        converted_messages = self._convert_messages(messages)
        converted_tools = self._convert_tools(config.tools) if config.tools else None

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": converted_messages,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "stream": True,
        }
        if converted_tools:
            kwargs["tools"] = converted_tools
            kwargs["tool_choice"] = "auto"

        stream = cast(Any, await self.client.chat.completions.create(**kwargs))  # type: ignore[arg-type]

        current_content = ""
        current_tool_calls: list[ToolCall] = []
        # Track partial argument strings by tool call index
        _tool_arg_buffers: dict[int, str] = {}
        _tool_ids: dict[int, str] = {}
        _tool_names: dict[int, str] = {}

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            # Text content
            if delta.content:
                current_content += delta.content
                yield {"type": "content_delta", "content": delta.content, "role": "assistant"}

            # Tool call deltas — OpenAI sends tool_calls with an index field
            # to identify which tool call each delta belongs to.
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index

                    # First delta for this tool call carries the id and name
                    if tc_delta.id:
                        _tool_ids[idx] = tc_delta.id
                    if tc_delta.function and tc_delta.function.name:
                        _tool_names[idx] = tc_delta.function.name
                        yield {
                            "type": "tool_call_delta",
                            "tool_call": {
                                "id": _tool_ids.get(idx, ""),
                                "name": tc_delta.function.name,
                                "input": None,
                            },
                        }

                    # Accumulate argument JSON fragments
                    if tc_delta.function and tc_delta.function.arguments:
                        _tool_arg_buffers[idx] = _tool_arg_buffers.get(idx, "") + tc_delta.function.arguments
                        yield {
                            "type": "tool_call_delta",
                            "tool_call": {
                                "id": _tool_ids.get(idx, ""),
                                "name": _tool_names.get(idx, ""),
                                "input_json": tc_delta.function.arguments,
                            },
                        }

            # Message complete — finalize accumulated tool calls.
            # OpenRouter's Claude route can emit finish_reason on more than one
            # chunk for the same turn; clear the accumulation dicts after
            # finalizing so a repeat finish_reason chunk doesn't re-append the
            # same tool calls (which would produce duplicate, non-unique ids).
            if chunk.choices[0].finish_reason:
                for idx in sorted(_tool_ids.keys()):
                    raw_json = _tool_arg_buffers.get(idx, "{}")
                    try:
                        arguments = json.loads(raw_json)
                    except json.JSONDecodeError:
                        arguments = {"_raw": raw_json}
                    current_tool_calls.append(
                        ToolCall(id=_tool_ids[idx], name=_tool_names.get(idx, ""), arguments=arguments)
                    )
                _tool_ids.clear()
                _tool_names.clear()
                _tool_arg_buffers.clear()

                yield {
                    "type": "message_complete",
                    "content": current_content,
                    "tool_calls": [tc.name for tc in current_tool_calls],
                    "finish_reason": chunk.choices[0].finish_reason,
                }

        # Populate the result container for the session to consume
        result.content = current_content
        result.tool_calls = current_tool_calls
