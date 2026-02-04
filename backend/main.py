import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.chat import router as chat_router
from app.core.config import settings
from app.core.llm import InMemorySessionStore

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application-wide resources."""
    app.state.http_client = httpx.AsyncClient()
    app.state.session_store = InMemorySessionStore(max_sessions=100, ttl_seconds=3600)
    yield
    await app.state.http_client.aclose()


app = FastAPI(
    title=settings.app.name,
    version=settings.app.version,
    description="Voice/text-controlled Roku system using LLMs",
    lifespan=lifespan,
)

app.include_router(chat_router)

# Configure application logging early
_level = getattr(logging, settings.logging.level.upper(), logging.INFO)
logging.basicConfig(level=_level, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("cueso")
logger.info(
    "Starting %s v%s (env=%s, host=%s, port=%s)",
    settings.app.name,
    settings.app.version,
    settings.app.environment,
    settings.server.host,
    settings.server.port,
)

# Configure CORS based on environment
if settings.app.environment == "production":
    cors_origins: list[str] = []
    cors_allow_credentials = False
    cors_allow_methods = ["GET", "POST", "PUT", "DELETE"]
    cors_allow_headers = ["Content-Type", "Authorization", "Accept"]
    cors_max_age = 86400  # 24 hours
else:
    cors_origins = ["*"]
    cors_allow_credentials = False
    cors_allow_methods = ["*"]
    cors_allow_headers = ["*"]
    cors_max_age = 0  # No caching in development

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=cors_allow_credentials,
    allow_methods=cors_allow_methods,
    allow_headers=cors_allow_headers,
    max_age=cors_max_age,
)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


# Serve frontend static files when the built frontend exists (production).
# In dev mode (no static/ directory), the app runs in API-only mode.
if STATIC_DIR.is_dir():
    logger.info("Serving frontend from %s", STATIC_DIR)

    assets_dir = STATIC_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="static-assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str) -> Response:
        """Serve static files or fall back to index.html for SPA routing."""
        file_path = STATIC_DIR / full_path
        if full_path and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

else:
    logger.info("No frontend at %s — API-only mode", STATIC_DIR)


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=settings.app.debug,
        reload_dirs=["app"],
        log_level=settings.logging.level.lower(),
    )
