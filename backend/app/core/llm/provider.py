"""LLM Provider abstract base class."""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

from .types import Message, SessionConfig, ToolCall


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
    ) -> AsyncGenerator[dict[str, Any]]:
        """Generate a streaming response from the LLM."""
        yield {}  # pragma: no cover
