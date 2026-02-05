"""WebSocket chat endpoint for LLM interactions."""

import json
import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from starlette.requests import HTTPConnection
from pydantic import BaseModel

from ..core.brave_search import BraveSearchClient
from ..core.config import settings
from ..core.llm import ROKU_ECP_PORT, LLMProvider, SessionConfig, SessionStore, ToolExecutor
from ..core.llm.tool_executor import RokuECPToolExecutor

logger = logging.getLogger("cueso.chat")

router = APIRouter()


SYSTEM_PROMPT = (
    "You are a helpful assistant that controls Roku devices. "
    "Use the available tools to help users find and play content.\n\n"
    "When a user asks to play content:\n"
    "1. If you're unsure about the exact title, season, or episode, use web_search "
    "to research it first.\n"
    "2. Once you know the exact content, call find_content to search streaming services.\n"
    "3. After find_content returns, present the available streaming services to the user "
    "and let them choose where to play. Do NOT automatically call launch_on_roku.\n"
    "4. When the user tells you which service to use, call launch_on_roku with that "
    "service's channel_id, content_id, and media_type.\n\n"
    "For general questions or when you need information, use web_search.\n"
    "For direct Roku operations, use search_roku or get_roku_status."
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
        raise ValueError("LLM API key is required. Set llm.api_key in config.yml")
    api_key = settings.llm.api_key.get_secret_value()

    if settings.llm.provider == "anthropic":
        from ..core.llm.providers.anthropic import AnthropicProvider

        return AnthropicProvider(api_key=api_key, model=settings.llm.model)
    elif settings.llm.provider == "openai":
        from ..core.llm.providers.openai import OpenAIProvider

        return OpenAIProvider(api_key=api_key, model=settings.llm.model)
    else:
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
        return RokuECPToolExecutor(settings.roku.ip, http_client, brave_client)
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
    http_client: httpx.AsyncClient = Depends(get_http_client),
):
    """Direct Roku launch endpoint for frontend use.

    Proxies a launch request to the Roku ECP API.
    """
    from ..core.search_and_play import launch_on_roku

    roku_base_url = f"http://{settings.roku.ip}:{ROKU_ECP_PORT}"
    result = await launch_on_roku(
        channel_id=channel_id,
        content_id=content_id,
        roku_base_url=roku_base_url,
        http_client=http_client,
        media_type=media_type,
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
