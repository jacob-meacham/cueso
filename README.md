# Cueso

A voice/text-controlled Roku system that uses LLMs to interpret natural language commands and execute them through the Roku ECP API.

## 🚀 **Progressive Development Approach**

We're building this progressively, starting with a minimal backend and adding features step by step.

### **Current Status: Phase 1 - Basic Backend**
- ✅ Basic FastAPI server
- ✅ Configuration management
- ✅ Simple in-memory storage
- ✅ Basic health endpoints
- ✅ Testing setup

### **Next Phases:**
1. 🔄 **Roku ECP Client** - Basic device communication
2. 🔄 **LLM Integration** - Simple query processing
3. 🔄 **Tool Calling** - Basic tool execution
4. 🔄 **Streaming Responses** - Real-time updates
5. 🔄 **Frontend** - Web interface
6. 🔄 **CLI** - Command-line interface

## 🛠️ **Quick Start (Backend Only)**

### Prerequisites
- Python 3.13+
- uv (Python package manager)

### Setup
1. **Install uv**:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Backend Setup**:
   ```bash
   cd backend
   uv sync --dev
   cp env.example .env
   # Edit .env with your Roku IP and LLM API key
   ```

3. **Run Backend**:
   ```bash
   uv run uvicorn main:app --reload
   ```

4. **Test**:
   ```bash
   uv run pytest
   ```

### Environment Variables

Create a `.env` file in the `backend/` directory with:

#### **Required:**
- `ROKU_IP`: Your Roku device's IP address
- `LLM_API_KEY`: Your LLM provider API key

#### **Optional (with defaults):**
- `DEBUG`: Set to `true` for development (default: `false`)
- `HOST`: Server host (default: `0.0.0.0`)
- `PORT`: Server port (default: `8000`)
- `LLM_PROVIDER`: LLM provider (default: `openai`)
- `LLM_MODEL`: LLM model (default: `gpt-4`)
- `LOG_LEVEL`: Logging level (default: `INFO`)

#### **Environment Configuration:**
- `ENVIRONMENT`: Set to `production` for production (default: `development`)
- `HOSTNAME`: Your domain/hostname (default: `localhost`)

**Development mode** (default):
- Origins: `http://localhost:3000`, `http://localhost:3001`, `http://localhost:8000`
- Credentials: `true`
- Methods: `GET`, `POST`, `PUT`, `DELETE`, `OPTIONS`
- Headers: `*` (all)
- Cache: `0` (no caching)

**Production mode**:
- Origins: `https://yourdomain.com`
- Credentials: `false`
- Methods: `GET`, `POST`, `PUT`, `DELETE`
- Headers: `Content-Type`, `Authorization`, `Accept`
- Cache: `86400` (24 hours)

## 📁 **Project Structure**

```
cueso/
├── backend/                 # FastAPI backend (Phase 1)
│   ├── app/
│   │   ├── core/           # Configuration and storage
│   │   └── ...
│   ├── tests/              # Test suite
│   ├── pyproject.toml      # Python dependencies
│   └── README.md           # Backend documentation
├── project.md              # Project requirements
└── technical-architecture.md  # Technical design
```

## 🎯 **Why Progressive Development?**

- **Easier to review** - Small, focused changes
- **Faster feedback** - Test each component individually
- **Better architecture** - Learn from each phase
- **Reduced complexity** - Build on working foundation

## 🔧 **Development Commands**

```bash
# Backend
cd backend
uv sync --dev              # Install dependencies
uv run uvicorn main:app --reload  # Run server

# Testing & Quality
uv run pytest              # Run tests
uv run pytest --cov=app   # Run tests with coverage
uv run ruff check .        # Lint code
uv run ruff format .       # Format code
uv run pyright .           # Type checking

# Quality checks
uv run ruff check . && uv run pyright .  # Lint + type check
uv run pytest --cov=app --cov-report=html  # Tests with HTML coverage

# Add new dependencies
uv add package_name        # Production dependency
uv add --dev package_name  # Development dependency
```

## 📚 **Documentation**

- [Project Requirements](project.md) - What we're building
- [Technical Architecture](technical-architecture.md) - How we're building it
- [Backend README](backend/README.md) - Backend-specific setup

## 🤝 **Contributing**

1. Focus on one phase at a time
2. Keep changes small and focused
3. Ensure tests pass before moving to next phase
4. Document decisions and trade-offs

---

**Current Focus**: Get the basic backend working and testable. Then we'll add Roku communication, LLM integration, and tool calling step by step.
