"""Data types for LLM interactions."""

from dataclasses import dataclass
from enum import Enum
from typing import Any


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


@dataclass
class Message:
    """A message in the conversation."""

    role: MessageRole
    content: str | list[dict[str, Any]]
    tool_calls: list[ToolCall] | None = None
    tool_results: list[ToolResult] | None = None


@dataclass
class SessionConfig:
    """Configuration for an LLM session."""

    system_prompt: str
    tools: list[Tool]
    max_tokens: int = 2048
    max_iterations: int = 20
    temperature: float = 0.7
    stream: bool = True
    pause_after: frozenset[str] = frozenset()
