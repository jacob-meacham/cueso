# Cueso Backend

Simple FastAPI backend for the Cueso project.

## Setup

1. **Install uv** (Python package manager):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Install dependencies**:
   ```bash
   uv sync --dev
   ```

3. **Configure environment**:
   ```bash
   cp env.example .env
   # Edit .env with your configuration
   ```

4. **Run the server**:
   ```bash
   uv run uvicorn main:app --reload
   ```

## Testing

Run tests with:
```bash
uv run pytest
```

## API Endpoints

- `GET /` - Root endpoint with basic info
- `GET /health` - Health check

## Development

This is a minimal version with just the basic FastAPI setup. We'll add features progressively:

1. ✅ Basic FastAPI app
2. 🔄 Roku ECP client
3. 🔄 LLM integration
4. 🔄 Tool calling system
5. 🔄 Streaming responses
