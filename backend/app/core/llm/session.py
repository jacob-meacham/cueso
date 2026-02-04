"""Session management for LLM conversations."""

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from .provider import LLMProvider
from .types import Message, MessageRole, SessionConfig, StreamResult, ToolCall, ToolCallStatus, ToolResult


class LLMSession:
    """Manages a conversation session with an LLM."""

    def __init__(
        self,
        session_id: str,
        provider: LLMProvider,
        config: SessionConfig,
    ):
        self.session_id = session_id
        self.provider = provider
        self.config = config
        self.messages: list[Message] = []
        self.iteration_count = 0

        # Add system message if provided
        if config.system_prompt:
            self.messages.append(Message(role=MessageRole.SYSTEM, content=config.system_prompt))
        self.logger = logging.getLogger(f"cueso.session.{session_id}")
        self.logger.debug(
            "Initialized session with system_prompt=%s, stream=%s", bool(config.system_prompt), config.stream
        )

    def add_message(self, message: Message) -> None:
        """Add a message to the session."""
        self.messages.append(message)

    def get_context(self) -> list[Message]:
        """Get the conversation context."""
        return self.messages.copy()

    async def chat(
        self,
        user_message: str,
        tool_executor: Callable[[ToolCall], Awaitable[str]] | None = None,
    ) -> AsyncGenerator[dict[str, Any]]:
        """Chat with the LLM, handling tool calls if needed.

        Implements the tool-calling loop: LLM → tool call → tool result → LLM.
        Each iteration is one LLM round-trip. The loop runs until:
        1. The LLM returns no tool calls (normal completion).
        2. max_iterations is reached (safety limit).
        3. A tool in pause_after is executed (hand control back to the client).
        4. No tool_executor is provided (caller handles tool calls externally).
        """
        self.add_message(Message(role=MessageRole.USER, content=user_message))
        self.logger.info("User message added; total_messages=%d", len(self.messages))

        content = ""
        tool_calls: list[ToolCall] = []

        while self.iteration_count < self.config.max_iterations:
            self.iteration_count += 1

            # Generate response from LLM
            if self.config.stream:
                stream_result = StreamResult()
                async for chunk in self.provider.generate_stream(self.get_context(), self.config, stream_result):
                    self.logger.debug("Stream chunk type=%s keys=%s", chunk.get("type"), list(chunk.keys()))
                    yield chunk

                content = stream_result.content
                tool_calls = stream_result.tool_calls
            else:
                content, tool_calls = await self.provider.generate(self.get_context(), self.config)

            # Add assistant message
            self.add_message(Message(role=MessageRole.ASSISTANT, content=content, tool_calls=tool_calls))
            self.logger.info("Assistant message added; tool_calls=%d", len(tool_calls or []))

            # If no tool calls, we're done
            if not tool_calls:
                break

            # Execute tool calls if we have a tool executor
            if tool_executor:
                tool_results: list[ToolResult] = []
                for tool_call in tool_calls:
                    try:
                        result = await tool_executor(tool_call)
                        self.logger.debug("Tool executed name=%s", tool_call.name)
                        tool_results.append(ToolResult(tool_call_id=tool_call.id, content=result))
                        yield {
                            "type": "tool_result",
                            "tool_name": tool_call.name,
                            "tool_call_id": tool_call.id,
                            "result": result,
                        }
                    except Exception as e:
                        self.logger.exception("Tool execution failed for %s: %s", tool_call.name, e)
                        tool_results.append(
                            ToolResult(
                                tool_call_id=tool_call.id,
                                content=f"Error: {e!s}",
                                status=ToolCallStatus.FAILED,
                                error=str(e),
                            )
                        )
                        yield {
                            "type": "tool_result",
                            "tool_name": tool_call.name,
                            "tool_call_id": tool_call.id,
                            "result": f"Error: {e!s}",
                            "error": True,
                        }

                # Add tool results to conversation
                self.add_message(Message(role=MessageRole.TOOL, content="", tool_results=tool_results))

                # If any executed tool is in pause_after, yield to the client
                if self.config.pause_after and any(tc.name in self.config.pause_after for tc in tool_calls):
                    self.logger.info("Pausing after tool(s): %s", self.config.pause_after)
                    break
            else:
                # No tool executor, break here
                break

        # Check if we paused mid-conversation
        paused = self.config.pause_after and any(tc.name in self.config.pause_after for tc in (tool_calls or []))

        # Yield final response
        yield {
            "type": "final",
            "content": content,
            "tool_calls": [tc.name for tc in (tool_calls or [])],
            "session_id": self.session_id,
            "iteration_count": self.iteration_count,
            "paused": bool(paused),
        }

    def reset(self) -> None:
        """Reset the session, keeping only the system message."""
        system_messages = [msg for msg in self.messages if msg.role == MessageRole.SYSTEM]
        self.messages = system_messages
        self.iteration_count = 0

    def get_system_prompt(self) -> str:
        """Get the system prompt from the session."""
        for msg in self.messages:
            if msg.role == MessageRole.SYSTEM:
                return msg.content if isinstance(msg.content, str) else ""
        return ""
