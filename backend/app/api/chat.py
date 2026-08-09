"""WebSocket chat endpoint for LLM interactions."""

import json
import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from starlette.requests import HTTPConnection

from ..core.brave_search import BraveSearchClient
from ..core.config import settings
from ..core.emby import EmbyClient
from ..core.llm import ROKU_ECP_PORT, LLMProvider, SessionConfig, SessionStore, ToolExecutor
from ..core.llm.tool_executor import RokuECPToolExecutor
from ..core.tmdb import TMDBClient

logger = logging.getLogger("cueso.chat")

router = APIRouter()


SYSTEM_PROMPT = (
    "You are a helpful assistant that controls Roku devices. The user talks to "
    "you for one reason: to watch something. Treat any question about a show, "
    "movie, or episode — even an indirect one like \"what's that show "
    'where..." — as a request to find and play that content.\n\n'
    "1. If you're unsure about the exact title, season, or episode, use web_search "
    "to research it first.\n"
    "2. Once you know the exact content, immediately call find_content to search "
    "the user's personal Emby library and streaming services. Never ask whether "
    "to search — always search. Identifying a title is never the end of your "
    'turn: after answering a "what show is that?" question, call find_content '
    "on that title in the same turn.\n"
    "3. IMPORTANT: every response that calls a tool must START with one short "
    "sentence of text (e.g. 'Searching for Inception...'), then the tool call. "
    "A bare tool call with no text is always wrong.\n"
    "4. After find_content returns, present the available options to the user and "
    "let them choose where to play. Emby is the user's own server — list it first "
    "when it has the content. Do NOT automatically call launch_on_roku.\n"
    "5. When the user tells you which service to use, call launch_on_roku with that "
    "service's channel_id, content_id, and media_type. For Emby matches, omit "
    "post_launch_key and pass resume_position_ticks when the match has one, so "
    "playback continues where the user left off.\n\n"
    "For direct Roku operations (what's playing, device status), use search_roku "
    "or get_roku_status."
)


class ChatMessage(BaseModel):
    """Chat message from client."""

    message: str
    session_id: str | None = None


# --- Dependency injection helpers ---


def get_session_store(conn: HTTPConnection) -> SessionStore:
    """Get the session store from app state."""
    return conn.app.state.session_store  # type: ignore[no-any-return]


def get_http_client(conn: HTTPConnection) -> httpx.AsyncClient:
    """Get the shared HTTP client from app state."""
    return conn.app.state.http_client  # type: ignore[no-any-return]


async def get_llm_provider() -> LLMProvider:
    """Get the configured LLM provider."""
    if not settings.llm.api_key:
        raise ValueError("LLM API key is required. Set llm.api_key in config.yml (an OpenRouter key)")
    api_key = settings.llm.api_key.get_secret_value()

    if settings.llm.provider == "openrouter":
        from ..core.llm.providers.openrouter import OpenRouterProvider

        return OpenRouterProvider(api_key=api_key, model=settings.llm.model, base_url=settings.llm.base_url)

    raise ValueError(f"Unsupported LLM provider: {settings.llm.provider}")


async def get_tool_executor(http_client: httpx.AsyncClient = Depends(get_http_client)) -> ToolExecutor:
    """Get the configured tool executor using the shared HTTP client."""
    if settings.tools.executor == "mcp":
        from ..core.llm.tool_executor import MCPToolExecutor

        # TODO: Initialize MCP client with proper configuration
        mcp_client = None  # Placeholder
        return MCPToolExecutor(mcp_client)
    elif settings.tools.executor == "roku_ecp":
        brave_client = None
        if settings.brave.api_key:
            brave_client = BraveSearchClient(
                api_key=settings.brave.api_key.get_secret_value(),
                http_client=http_client,
            )
        emby_client = None
        if settings.emby.server_url and settings.emby.api_key:
            emby_client = EmbyClient(
                server_url=settings.emby.server_url,
                api_key=settings.emby.api_key.get_secret_value(),
                user_id=settings.emby.user_id,
                http_client=http_client,
            )
        tmdb_client = None
        if settings.tmdb.api_key:
            tmdb_client = TMDBClient(
                api_key=settings.tmdb.api_key.get_secret_value(),
                region=settings.tmdb.region,
                http_client=http_client,
            )
        return RokuECPToolExecutor(settings.roku.ip, http_client, brave_client, emby_client, tmdb_client)
    else:
        raise ValueError(f"Unsupported tool executor: {settings.tools.executor}")


@router.websocket("/ws/chat")
async def websocket_chat(
    websocket: WebSocket,
    provider: LLMProvider = Depends(get_llm_provider),
    tool_executor: ToolExecutor = Depends(get_tool_executor),
):
    """WebSocket endpoint for chat with LLM."""
    # Validate origin if allowed_origins is configured (empty list = allow all)
    allowed = settings.app.allowed_origins
    if allowed:
        origin = websocket.headers.get("origin")
        if origin and origin not in allowed:
            await websocket.close(code=4003, reason="Origin not allowed")
            return

    await websocket.accept()
    logger.info("WebSocket connected")

    session_store: SessionStore = websocket.app.state.session_store

    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            logger.debug("Received raw payload: %s", data)
            chat_data = json.loads(data)
            chat_message = ChatMessage(**chat_data)
            logger.info(
                "Incoming message session_id=%s len=%s",
                chat_message.session_id,
                len(chat_message.message or ""),
            )

            # Generate session ID if not provided
            session_id = chat_message.session_id or str(uuid.uuid4())
            logger.debug("Using session_id=%s", session_id)

            # Get or create session
            session = session_store.get_session(session_id)
            if not session:
                config = SessionConfig(
                    system_prompt=SYSTEM_PROMPT,
                    tools=RokuECPToolExecutor.get_tool_definitions(),
                    max_tokens=2048,
                    max_iterations=10,
                    temperature=0.7,
                    stream=True,
                    pause_after=frozenset({"find_content"}),
                )
                session = session_store.create_session(
                    session_id=session_id,
                    provider=provider,
                    config=config,
                )
                logger.info("Created new session: %s", session_id)

            # Send session ID back to client
            await websocket.send_text(json.dumps({"type": "session_created", "session_id": session_id}))
            logger.debug("Sent session_created for %s", session_id)

            # Chat with LLM — all tools are normal (no interrupt pattern)
            logger.info("Starting chat loop for session=%s", session_id)
            async for event in session.chat(
                user_message=chat_message.message,
                tool_executor=tool_executor.execute_tool,
            ):
                await websocket.send_text(json.dumps(event))
                logger.debug("Emitted event: %s", event.get("type"))

                if event["type"] == "final":
                    logger.info("Final event for session=%s", session_id)
                    break

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.exception("Error in WebSocket: %s", e)
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass


@router.post("/roku/launch")
async def roku_launch(
    channel_id: int,
    content_id: str,
    media_type: str = "movie",
    resume_position_ticks: int | None = None,
    http_client: httpx.AsyncClient = Depends(get_http_client),
):
    """Direct Roku launch endpoint for frontend use.

    Proxies a launch request to the Roku ECP API. resume_position_ticks is
    honored for Emby launches so playback continues where the user left off.
    """
    from ..core.search_and_play import launch_on_roku

    roku_base_url = f"http://{settings.roku.ip}:{ROKU_ECP_PORT}"
    result = await launch_on_roku(
        channel_id=channel_id,
        content_id=content_id,
        roku_base_url=roku_base_url,
        http_client=http_client,
        media_type=media_type,
        resume_position_ticks=resume_position_ticks,
    )
    return {"success": result.success, "message": result.message}


@router.get("/chat/sessions")
async def list_sessions(store: SessionStore = Depends(get_session_store)):
    """List all active chat sessions."""
    sessions = store.list_sessions()
    return {
        "sessions": sessions,
        "count": len(sessions),
    }


@router.delete("/chat/sessions/{session_id}")
async def delete_session(session_id: str, store: SessionStore = Depends(get_session_store)):
    """Delete a chat session."""
    store.delete_session(session_id)
    return {"message": f"Session {session_id} deleted"}


@router.post("/chat/sessions/{session_id}/reset")
async def reset_session(session_id: str, store: SessionStore = Depends(get_session_store)):
    """Reset a chat session."""
    session = store.get_session(session_id)
    if session:
        session.reset()
        return {"message": f"Session {session_id} reset"}
    else:
        return {"error": f"Session {session_id} not found"}
