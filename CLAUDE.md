# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Cueso is a voice/text-controlled Roku system that uses LLMs to interpret natural language commands and execute them through the Roku ECP API. Built with FastAPI (Python 3.13+) backend, a Click-based CLI, and a planned Next.js frontend.

## Development Commands

All backend commands run from the `backend/` directory using `uv`:

```bash
# Install dependencies
uv sync --dev

# Run dev server (reads config.yml from repo root)
uv run python main.py

# Run all tests (includes coverage by default via pyproject.toml)
uv run pytest

# Run a single test file
uv run pytest tests/test_basic.py

# Run a single test function
uv run pytest tests/test_basic.py::test_health_endpoint

# Run tests by marker
uv run pytest -m unit
uv run pytest -m "not slow"

# Lint and format
uv run ruff check .
uv run ruff format .

# Type checking (strict mode)
uv run pyright .

# Add dependencies
uv add package_name
uv add --dev package_name

```

CLI commands run from the `cli/` directory:
```bash
uv sync
uv run python cli/main.py

# Run evals (requires dev server running via backend/dev.sh)
./evals.sh              # all evals
./evals.sh 1 3 6        # specific evals by number
./evals.sh --list       # list available evals
```

## Architecture

The system follows a WebSocket-first streaming architecture:

```
User Input (CLI/Web) → WebSocket /ws/chat → LLMSession → LLMProvider → Tool Executor → Roku Device
```

**Backend** (`backend/app/`):
- `core/config.py` — Pydantic BaseSettings configuration, loaded from `config.yml` (YAML source with env var overrides)
- `core/llm/provider.py` — Abstract base class for LLM providers (`generate`, `generate_stream`)
- `core/llm/providers/` — Concrete implementations: `anthropic.py`, `openai.py`
- `core/llm/session.py` — Manages conversation state and the tool-calling loop (LLM → tool call → tool result → LLM, up to `max_iterations`)
- `core/llm/session_store.py` — Session persistence interface with in-memory implementation
- `core/llm/tool_executor.py` — Tool execution strategies: `RokuECPToolExecutor` (direct HTTP), `MCPToolExecutor` (Model Context Protocol)
- `core/llm/types.py` — Core data models: Message, Tool, ToolCall, ToolResult, SessionConfig
- `api/chat.py` — WebSocket endpoint, REST session management, tool definitions

**CLI** (`cli/cli/`):
- Click-based terminal app using Rich and prompt_toolkit
- Connects to backend via WebSocket

**Streaming event types** (server → client): `session_created`, `content_delta`, `tool_call_delta`, `message_complete`, `final`, `error`

## Code Quality Configuration

- **Ruff**: Line length 120, target Python 3.13, rules: E, F, I, N, W, B, C4, UP, RUF. E501 and B008 ignored. Unused imports allowed in `__init__.py`.
- **Pyright**: Strict mode, Python 3.13.
- **pytest**: Strict markers/config, coverage on `app` package. Markers: `slow`, `integration`, `unit`.

## Key Patterns

- **Provider pattern**: New LLM providers implement the `LLMProvider` ABC. Provider selection via `llm.provider` in config.
- **Tool executor pattern**: New tool backends implement `ToolExecutor` ABC. Selected via `tools.executor` in config.
- **Configuration**: All config via `config.yml` (repo root) using Pydantic Settings with `YamlConfigSettingsSource`. Env vars override YAML using `__` nesting (e.g. `LLM__PROVIDER=openai`). Config file path overridable via `CUESO_CONFIG` env var. See `config.yml.example`. In Docker, `config.yml` is mounted read-only into the container.
- **Streaming service priority**: Configurable in `streaming` (a list) — controls which services are active and their match order. Service definitions (regex, channel IDs) stay in code.
- **Async throughout**: All I/O is async/await. Tests use `pytest-asyncio`.

## Workflow Rules (from .cursor/coding_process.mdc)

- **Plan before code**: Develop an implementation plan (goal, assumptions, affected files, steps, considerations) and get approval before writing code.
- **Change-build-debug-fix loop**: Run build and tests after code changes. Fix root causes, not symptoms. Keep changes scoped and minimal.
- **Commit discipline**: Small, focused commits after each fix. Document unresolved issues in next-steps.md.
