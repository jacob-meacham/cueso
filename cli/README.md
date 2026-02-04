# Cueso CLI Frontend

A Python-based command-line interface for the Cueso backend system.

## Features

- Interactive console chat with LLM
- Session management (create, list, switch)
- WebSocket streaming for real-time responses
- Rich terminal output with syntax highlighting

## Installation

```bash
cd cli
uv sync
```

## Usage

```bash
uv run python cli/main.py
```

## Commands

- `/list` - List all available sessions
- `/session [id]` - Switch to existing session or create new one
- `/exit` or `Ctrl+C` - Exit the application
- Any other input - Send message to current session

## Configuration

The CLI connects to the backend at `http://localhost:8000` by default. You can modify this in the configuration.
