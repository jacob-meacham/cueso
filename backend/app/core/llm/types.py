"""Data types for LLM interactions."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Named constants for magic numbers
ROKU_ECP_PORT = 8060
DEFAULT_MAX_TOKENS = 2048
DEFAULT_MAX_ITERATIONS = 20
DEFAULT_TEMPERATURE = 0.7


class MessageRole(str, Enum):
    """Message roles in a conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCallStatus(str, Enum):
    """Status of a tool call."""

    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Tool:
    """Definition of a tool that can be called by the LLM."""

    name: str
    description: str
    input_schema: dict[str, Any]
    required: list[str] | None = None


@dataclass
class ToolCall:
    """A tool call request from the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    """Result of a tool call execution."""

    tool_call_id: str
    content: str
    status: ToolCallStatus = ToolCallStatus.COMPLETED
    error: str | None = None


# Self-documenting type alias for message content:
# - str for simple text content
# - list[dict[str, Any]] for structured content blocks (e.g., Anthropic text/tool_use blocks)
MessageContent = str | list[dict[str, Any]]


@dataclass
class Message:
    """A message in the conversation."""

    role: MessageRole
    content: MessageContent
    tool_calls: list[ToolCall] | None = None
    tool_results: list[ToolResult] | None = None


@dataclass
class StreamResult:
    """Accumulated result from a streaming LLM call."""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=lambda: list[ToolCall]())


@dataclass
class SessionConfig:
    """Configuration for an LLM session."""

    system_prompt: str
    tools: list[Tool]
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    temperature: float = DEFAULT_TEMPERATURE
    stream: bool = True
    pause_after: frozenset[str] = frozenset()
