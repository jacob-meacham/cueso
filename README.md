# Cueso

Voice/text-controlled Roku using LLMs. FastAPI backend, React PWA frontend, CLI.

## Prerequisites

- Python 3.13+ and [uv](https://docs.astral.sh/uv/)
- Node.js 22+

## Quick Start

    cp config.yml.example config.yml
    # edit config.yml — add your LLM API key and Roku IP
    ./scripts/dev

Backend at http://localhost:8483, frontend at http://localhost:8484.

## Commands

    # Development (install deps + start both services)
    ./scripts/dev

    # Build frontend + run all backend quality checks and tests
    ./scripts/build

    # Docker
    docker compose up --build

## Configuration

### Backend

Copy `config.yml.example` to `config.yml` and fill in:
- `llm.api_key` — your Anthropic or OpenAI API key
- `roku.ip` — your Roku device's IP address
- `brave.api_key` — Brave Search API key (for content search)

Settings can be overridden with env vars using `__` nesting (e.g. `LLM__PROVIDER=openai`).
See the example file for all available options.

### Frontend

For development, set the backend URL in `frontend/.env.development`:

    VITE_API_URL=http://localhost:8483
    VITE_WS_URL=ws://localhost:8483

In production (Docker), the frontend is served by the backend on the same origin, so these are not needed.

## Architecture

The system uses a WebSocket-first streaming architecture:

    User Input (CLI/Web) → WebSocket /ws/chat → LLMSession → LLM Provider → Tool Executor → Roku

- **Backend** (`backend/`): FastAPI with async tool-calling loop, Roku ECP integration, Brave Search
- **Frontend** (`frontend/`): React PWA with streaming chat UI, voice input, content cards
- **CLI** (`cli/`): Click-based terminal client connecting via WebSocket

See `docs/` for detailed design documents.

## Docker

    docker compose up --build       # start
    docker compose down             # stop
    docker compose up --build -d    # start detached

Backend serves both API and frontend on :8484.
All configuration (including API keys) lives in `config.yml`.

## Evaluations

The CLI includes an eval system for testing end-to-end behavior against a running backend.

    cd cli
    ./evals.sh              # run all evals
    ./evals.sh 1 3 6        # run specific evals by number
    ./evals.sh --list       # list available evals

Evals require the dev server running (`./scripts/dev` or `cd backend && uv run python main.py`).
Each eval sends a prompt to the backend and validates the response against expected patterns.

## Troubleshooting

**`config.yml not found`**: Copy `config.yml.example` to `config.yml` in the repo root. The backend
looks for this file relative to its working directory (`backend/`), so it reads `../config.yml` or
you can set `CUESO_CONFIG=/absolute/path/to/config.yml`.

**`LLM API key is required`**: Set `llm.api_key` in `config.yml` or via env var `LLM__API_KEY=sk-...`.

**`Roku device returned status 500` / connection refused**: Verify your Roku is on the same network
and `roku.ip` in config matches. The Roku ECP API listens on port 8060 by default.

**Port already in use**: The backend defaults to port 8483. Change it in `config.yml` under
`server.port` or via `SERVER__PORT=9000`.
