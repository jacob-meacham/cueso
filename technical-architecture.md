# Cueso Technical Architecture

## System Overview

Cueso is a voice/text-controlled Roku system that uses LLMs to interpret natural language commands and execute them through the Roku ECP API. The system consists of a backend API, web frontend (PWA), CLI interface, and integrates with external media metadata services.

## High-Level Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Voice/Text    │    │   Web Frontend  │    │   CLI Frontend  │
│   Input         │    │   (PWA)         │    │                 │
└─────────┬───────┘    └─────────┬───────┘    └─────────┬───────┘
          │                      │                      │
          └──────────────────────┼──────────────────────┘
                                 │
                    ┌─────────────▼─────────────┐
                    │      Backend API          │
                    │   (FastAPI/Next.js)      │
                    └─────────────┬─────────────┘
                                 │
          ┌───────────────────────┼───────────────────────┐
          │                       │                       │
┌─────────▼─────────┐  ┌─────────▼─────────┐  ┌─────────▼─────────┐
│   LLM Router      │  │   Media Search    │  │   Roku ECP       │
│   (LangChain)     │  │   (TMDb/OMDB)     │  │   API Client     │
└─────────┬─────────┘  └─────────┬─────────┘  └─────────┬─────────┘
          │                      │                      │
          └──────────────────────┼──────────────────────┘
                                 │
                    ┌─────────────▼─────────────┐
                    │      Roku Device         │
                    │   (Local Network)        │
                    └───────────────────────────┘
```

## Core Components

### 1. Backend API
- **Framework**: FastAPI with Python 3.11+
- **Purpose**: Orchestrate LLM interactions, manage tool calls, handle Roku communication
- **Key Responsibilities**:
  - LLM provider management (static configuration)
  - Tool calling orchestration with streaming responses
  - Roku ECP API communication
  - Media metadata aggregation
  - User preference context management

### 2. LLM Router
- **Implementation**: LangChain with async tool calling
- **Purpose**: Route requests to configured LLM provider
- **Configuration**: Static provider selection at startup
- **Supported Providers**:
  - OpenAI (GPT-4)
  - Anthropic (Claude)
  - Local models (Ollama, etc.)
  - Pluggable provider interface

### 3. Tool Calling System
- **Purpose**: Enable LLMs to execute actions through structured tools
- **Core Tools**:
  - `search_media`: Query TMDb/OMDB for movie/show information
  - `search_roku`: Search Roku channels for specific content
  - `play_content`: Execute playback on Roku
  - `get_roku_status`: Check current Roku state

### 4. Media Search Integration
- **TMDb API**: Movie and TV show metadata
- **OMDB API**: Additional movie information
- **Purpose**: Enrich user queries with metadata before Roku search

### 5. Roku ECP Client
- **Protocol**: Roku External Control Protocol
- **Features**:
  - Device discovery (optional)
  - App launching
  - Content search within apps
  - Playback control
  - Device status monitoring

### 6. Storage Interface
- **Implementation**: Abstract interface with SQLite backend
- **Purpose**: Flexible storage for preferences, queries, and caching
- **Features**:
  - User preferences storage
  - Query history and results
  - Media metadata caching
  - LLM response caching
  - Pluggable backend support

## Data Models

### User Preferences
```typescript
interface UserPreferences {
  id: string;
  preferredChannels: string[];
  channelPriorities: Record<string, number>;
  defaultContentTypes: Record<string, string>;
  voiceSettings: {
    model: string;
    language: string;
    speed: number;
  };
  llmSettings: {
    provider: string;
    model: string;
    apiKey?: string;
  };
}
```

### Media Query
```typescript
interface MediaQuery {
  id: string;
  userId: string;
  query: string;
  queryType: 'voice' | 'text';
  status: 'pending' | 'processing' | 'completed' | 'failed';
  steps: QueryStep[];
  result?: MediaResult;
  createdAt: Date;
  completedAt?: Date;
}
```

### Query Step
```typescript
interface QueryStep {
  id: string;
  queryId: string;
  stepNumber: number;
  toolName: string;
  input: Record<string, any>;
  output?: Record<string, any>;
  status: 'pending' | 'executing' | 'completed' | 'failed';
  error?: string;
  duration?: number;
}
```

### Media Result
```typescript
interface MediaResult {
  id: string;
  queryId: string;
  mediaType: 'movie' | 'tv_show' | 'episode';
  title: string;
  year?: number;
  channel: string;
  rokuContentId: string;
  metadata: {
    tmdbId?: string;
    omdbId?: string;
    description?: string;
    cast?: string[];
    genre?: string[];
    rating?: string;
  };
}
```

## API Endpoints

### Core Endpoints
```
POST   /api/query              # Submit voice/text query
GET    /api/query/{id}         # Get query status and result
GET    /api/query/{id}/steps   # Get detailed execution steps

GET    /api/preferences         # Get user preferences
PUT    /api/preferences         # Update user preferences

GET    /api/roku/status        # Get Roku device status
GET    /api/roku/channels      # Get available channels
POST   /api/roku/search        # Search Roku content

GET    /api/media/search        # Search external media APIs
```

### WebSocket Endpoints
```
/ws/query/{id}                 # Real-time query progress updates
/ws/roku/status                # Roku device status updates
```

## Streaming Responses

### LLM Response Streaming
- **Purpose**: Provide real-time feedback during tool execution
- **Implementation**: Server-Sent Events (SSE) or WebSocket
- **Stream Content**:
  - LLM thinking process
  - Tool execution status
  - Step-by-step progress
  - Final results and actions taken

### Frontend Integration
- **Web Frontend**: Real-time updates via SSE/WebSocket
- **CLI Frontend**: Streaming output to terminal
- **User Experience**: Immediate feedback and transparency

## Tool Calling Flow

### Example: "Play Severance Season 1 Episode 3"

1. **Query Reception**
   - User submits voice/text query
   - Backend creates MediaQuery record
   - LLM router selects appropriate provider

2. **Media Identification**
   - LLM calls `search_media` tool
   - Tool queries TMDb for "Severance" show details
   - Returns show ID, seasons, episodes info

3. **Roku Search**
   - LLM calls `search_roku` tool
   - Tool searches Apple TV+ app for "Severance"
   - Returns available content and rokuContentId

4. **Content Playback**
   - LLM calls `play_content` tool
   - Tool launches Apple TV+ app and navigates to specific episode
   - Updates query status to completed

5. **Result Return**
   - Backend updates MediaQuery with result
   - Frontend receives real-time updates via WebSocket
   - User sees confirmation and playback begins

## Technology Stack

### Backend
- **Language**: Python 3.11+
- **Framework**: FastAPI with async/await support
- **Dependency Management**: uv with pyproject.toml
- **Database**: SQLite with abstract storage interface for flexibility
- **Caching**: SQLite-based response and metadata caching
- **LLM Router**: LangChain with async tool calling
- **Async Processing**: FastAPI background tasks + asyncio

### Frontend
- **Framework**: Next.js 14+ with TypeScript
- **UI Library**: Tailwind CSS + Headless UI
- **State Management**: Zustand or Redux Toolkit
- **Voice Processing**: Web Speech API + Web Audio API (web only)
- **Text Input**: Web frontend and CLI interface
- **PWA**: Next.js PWA plugin
- **Streaming**: Real-time LLM response streaming via WebSocket/SSE

### Infrastructure
- **Containerization**: Docker + Docker Compose
- **CI/CD**: GitHub Actions
- **Deployment**: Docker containers
- **Monitoring**: Basic logging + health checks

## Security Considerations

- **API Keys**: Secure storage of external API keys
- **Local Network**: Roku communication limited to local network
- **User Data**: Minimal data collection, local storage preferred
- **LLM Providers**: Secure API key management for external LLMs

## Python Project Structure

### Backend Organization
```
backend/
├── pyproject.toml          # Project configuration and dependencies
├── src/
│   └── cueso/             # Main package
│       ├── __init__.py
│       ├── api/            # FastAPI routers
│       ├── core/           # Configuration and core utilities
│       ├── models/         # Pydantic data models
│       ├── services/       # Business logic services
│       └── tools/          # LLM tool implementations
├── tests/                  # Test suite
├── .env.example            # Environment configuration
└── README.md               # Development documentation
```

### Modern Python Tooling
- **uv**: Fast dependency resolution and virtual environment management
- **pyproject.toml**: Single source of truth for project configuration
- **src layout**: Clean package structure for distribution
- **Type hints**: Full type annotation for better development experience
- **Async patterns**: Modern async/await throughout the codebase

### Common uv Commands
```bash
# Install dependencies
uv sync                    # Install production dependencies
uv sync --dev             # Install development dependencies

# Run commands
uv run pytest             # Run tests
uv run black .            # Format code
uv run isort .            # Sort imports
uv run ruff check .       # Lint code
uv run mypy .             # Type checking

# Add dependencies
uv add fastapi            # Add production dependency
uv add --dev pytest       # Add development dependency

# Update dependencies
uv lock --upgrade         # Update lock file
```

## Performance & Scalability

- **Caching**: SQLite-based response and metadata caching
- **Async Processing**: FastAPI background tasks + asyncio for concurrent operations
- **Connection Pooling**: HTTP client connection reuse for external APIs
- **Rate Limiting**: Protect external API endpoints
- **Streaming**: Real-time response streaming for better user experience

## Development Phases

### Phase 1: Core Backend (Python + FastAPI)
- **Project Setup**: uv + pyproject.toml configuration
- **API Structure**: FastAPI with async endpoints
- **Roku ECP Client**: Async HTTP client for device control
- **Basic LLM Integration**: LangChain setup with async tools
- **Storage Layer**: SQLite with abstract interface
- **Testing**: pytest setup with async testing

### Phase 2: Media Integration & Tool Calling
- **TMDb/OMDB Integration**: Async API clients with caching
- **Enhanced Tool Calling**: LangChain async tool execution
- **Query Execution Tracking**: Real-time step monitoring
- **Streaming Responses**: Server-Sent Events implementation
- **Error Handling**: Comprehensive async error management

### Phase 3: Frontend & Integration
- **Web Interface**: Next.js with TypeScript
- **Voice Input Processing**: Web Speech API integration
- **Real-time Updates**: WebSocket/SSE frontend integration
- **API Integration**: Frontend-backend communication
- **Testing**: Frontend testing with React Testing Library

### Phase 4: Advanced Features & Polish
- **PWA Capabilities**: Service worker and offline support
- **CLI Interface**: Rich CLI with async operations
- **Advanced Preferences**: User context management
- **Multi-user Hooks**: Architecture preparation
- **Performance Optimization**: Caching and async improvements

## Python Development Tooling

### Dependency Management
- **uv**: Fast Python package installer and resolver
- **pyproject.toml**: Modern Python project configuration
- **Virtual Environments**: Automatic environment management with uv

### Code Quality
- **Black**: Uncompromising code formatter
- **isort**: Import sorting and organization
- **flake8**: Linting and style checking
- **mypy**: Static type checking
- **pre-commit**: Git hooks for code quality

### Testing & Development
- **pytest**: Testing framework with async support
- **pytest-asyncio**: Async testing utilities
- **pytest-cov**: Coverage reporting
- **httpx**: Async HTTP client for testing
- **pytest-mock**: Mocking utilities

## Testing Strategy

- **Unit Tests**: 90%+ coverage target with pytest
- **Integration Tests**: API endpoint testing with TestClient
- **E2E Tests**: Full query flow testing
- **Mock Services**: Roku and media API mocking
- **Performance Tests**: Tool calling latency testing
- **Async Testing**: Proper async/await testing patterns
