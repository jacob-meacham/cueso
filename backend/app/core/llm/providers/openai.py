"""OpenAI LLM provider implementation."""

import asyncio
import threading
from collections.abc import AsyncGenerator
from typing import Any, cast

import openai

from ..provider import LLMProvider
from ..types import Message, MessageRole, SessionConfig, Tool, ToolCall


class OpenAIProvider(LLMProvider):
    """OpenAI GPT provider implementation."""

    def __init__(self, api_key: str, model: str = "gpt-4"):
        self.client = openai.OpenAI(api_key=api_key)
        self.model = model

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert our Message format to OpenAI's format."""
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
                converted.append(
                    {"role": "assistant", "content": msg.content if isinstance(msg.content, str) else str(msg.content)}
                )
            elif msg.role == MessageRole.TOOL:
                # OpenAI uses 'function' role for tool results
                if msg.tool_results:
                    for tool_result in msg.tool_results:
                        converted.append(
                            {
                                "role": "function",
                                "name": "tool_result",  # We'll need to track which tool this came from
                                "content": tool_result.content,
                            }
                        )

        return converted

    def _convert_tools(self, tools: list[Tool]) -> list[dict[str, Any]]:
        """Convert our Tool format to OpenAI's function calling format."""
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

        # Handle function calls
        if response.choices[0].message.tool_calls:
            for tool_call in response.choices[0].message.tool_calls:
                tool_calls.append(
                    ToolCall(id=tool_call.id, name=tool_call.function.name, arguments=tool_call.function.arguments)
                )

        return content, tool_calls

    async def generate(
        self,
        messages: list[Message],
        config: SessionConfig,
    ) -> tuple[str, list[ToolCall]]:
        """Generate a non-streaming response from OpenAI."""
        # Convert messages and tools
        converted_messages = self._convert_messages(messages)
        converted_tools = self._convert_tools(config.tools) if config.tools else None

        # Prepare function calling if tools are provided
        function_call = "auto" if converted_tools else None

        # Make API call
        response = self.client.chat.completions.create(
            model=self.model,
            messages=cast(Any, converted_messages),
            tools=cast(Any, converted_tools),
            tool_choice=cast(Any, function_call),
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )

        return self._parse_response(response)

    async def generate_stream(
        self,
        messages: list[Message],
        config: SessionConfig,
    ) -> AsyncGenerator[dict[str, Any]]:
        """Generate a streaming response from OpenAI."""
        # Convert messages and tools
        converted_messages = self._convert_messages(messages)
        converted_tools = self._convert_tools(config.tools) if config.tools else None

        # Prepare function calling if tools are provided
        function_call = "auto" if converted_tools else None

        # Make streaming API call (sync stream object)
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=cast(Any, converted_messages),
            tools=cast(Any, converted_tools),
            tool_choice=cast(Any, function_call),
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            stream=True,
        )

        current_content = ""
        current_tool_calls: list[ToolCall] = []

        # Bridge sync iterator into async context using a background thread + asyncio.Queue
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()

        def worker() -> None:
            try:
                for chunk in stream:
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)  # Sentinel

        threading.Thread(target=worker, daemon=True).start()

        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            if chunk.choices[0].delta.content:
                current_content += chunk.choices[0].delta.content
                yield {"type": "content_delta", "content": chunk.choices[0].delta.content, "role": "assistant"}

            # Handle tool call deltas
            if chunk.choices[0].delta.tool_calls:
                for tool_call_delta in chunk.choices[0].delta.tool_calls:
                    tool_call_id = tool_call_delta.id or ""

                    # Find existing tool call or create new one
                    existing_tool_call = next((tc for tc in current_tool_calls if tc.id == tool_call_id), None)

                    if existing_tool_call:
                        # Update existing tool call
                        if tool_call_delta.function:
                            if tool_call_delta.function.name:
                                existing_tool_call.name = tool_call_delta.function.name
                            if tool_call_delta.function.arguments:
                                # Parse arguments if they're a string
                                if isinstance(tool_call_delta.function.arguments, str):
                                    import json

                                    try:
                                        args = json.loads(tool_call_delta.function.arguments)
                                        # Ensure arguments is a dict
                                        if isinstance(existing_tool_call.arguments, str):
                                            existing_tool_call.arguments = {"partial": existing_tool_call.arguments}
                                        existing_tool_call.arguments.update(args)
                                    except json.JSONDecodeError:
                                        # Handle partial JSON
                                        if isinstance(existing_tool_call.arguments, str):
                                            existing_tool_call.arguments = {"partial": existing_tool_call.arguments}
                                        existing_tool_call.arguments["partial"] = tool_call_delta.function.arguments
                                else:
                                    coerced: dict[str, Any] = {"value": tool_call_delta.function.arguments}
                                    if isinstance(existing_tool_call.arguments, str):
                                        existing_tool_call.arguments = {"partial": existing_tool_call.arguments}
                                    existing_tool_call.arguments.update(coerced)
                    else:
                        # Create new tool call
                        import json

                        raw_args: str | None = tool_call_delta.function.arguments if tool_call_delta.function else None
                        coerced_args: dict[str, Any]
                        if isinstance(raw_args, str):
                            try:
                                coerced_args = json.loads(raw_args)
                            except json.JSONDecodeError:
                                coerced_args = {"partial": raw_args}
                        else:
                            coerced_args = {}

                        new_tool_call = ToolCall(
                            id=tool_call_id,
                            name=tool_call_delta.function.name if tool_call_delta.function else "",
                            arguments=coerced_args,
                        )
                        current_tool_calls.append(new_tool_call)

                    yield {
                        "type": "tool_call_delta",
                        "tool_call": {
                            "id": tool_call_id,
                            "name": tool_call_delta.function.name if tool_call_delta.function else "",
                            "input": tool_call_delta.function.arguments if tool_call_delta.function else {},
                        },
                    }

            # Check if message is complete
            if chunk.choices[0].finish_reason:
                yield {
                    "type": "message_complete",
                    "content": current_content,
                    "tool_calls": [tc.name for tc in current_tool_calls],
                    "finish_reason": chunk.choices[0].finish_reason,
                }
