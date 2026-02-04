"""LLM Provider abstract base class."""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

from .types import Message, SessionConfig, StreamResult, ToolCall


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def generate(
        self,
        messages: list[Message],
        config: SessionConfig,
    ) -> tuple[str, list[ToolCall]]:
        """Generate a response from the LLM."""
        pass

    @abstractmethod
    async def generate_stream(
        self,
        messages: list[Message],
        config: SessionConfig,
        result: StreamResult,
    ) -> AsyncGenerator[dict[str, Any]]:
        """Generate a streaming response from the LLM.

        Yields event dicts for the client and populates `result` with
        the accumulated content and tool_calls by the time the generator
        is exhausted.
        """
        yield {}  # pragma: no cover
