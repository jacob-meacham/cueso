"""WebSocket chat endpoint for LLM interactions."""

import json
import logging
import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..core.config import settings
from ..core.llm import InMemorySessionStore, LLMProvider, SessionConfig, Tool, ToolExecutor

logger = logging.getLogger("cueso.chat")

router = APIRouter()

# Global session store instance
session_store = InMemorySessionStore()


# --- Tool definitions ---

AVAILABLE_TOOLS = [
    Tool(
        name="search_roku",
        description="Search for content on Roku channels",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "channel": {"type": "string", "description": "Channel to search"},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="get_roku_status",
        description="Get current status of Roku device",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    Tool(
        name="web_search",
        description=(
            "Search the web using Brave Search. Use this to find information about shows, "
            "movies, episodes, or any general knowledge. You can search IMDB, TVDB, Wikipedia, "
            "or any other site to identify content, confirm titles, and look up season/episode "
            "numbers. Returns titles, URLs, and descriptions."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of results to return (1-10, default 5)",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="find_content",
        description=(
            "Search streaming services (Netflix, Hulu, Disney+, Max, Apple TV+, Amazon Prime) "
            "for content and return all available matches with channel IDs and content IDs. "
            "Use this when you know the exact content to find. The results include every "
            "streaming service where the content is available. After calling this, use "
            "launch_on_roku to play the best match (or ask the user which service they prefer)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "The show or movie title (e.g. 'Rick and Morty')",
                },
                "season": {
                    "type": "integer",
                    "description": "Season number (for TV episodes)",
                },
                "episode": {
                    "type": "integer",
                    "description": "Episode number (for TV episodes)",
                },
                "episode_title": {
                    "type": "string",
                    "description": "Episode title for better search accuracy",
                },
                "media_type": {
                    "type": "string",
                    "description": "The type of media",
                    "enum": ["movie", "series", "episode", "season"],
                },
            },
            "required": ["title"],
        },
    ),
    Tool(
        name="launch_on_roku",
        description=(
            "Launch content on the Roku device. Call this after find_content with one of the "
            "returned matches. Provide the channel_id, content_id, and media_type from the "
            "find_content results."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "integer",
                    "description": "Roku channel ID from find_content results",
                },
                "content_id": {
                    "type": "string",
                    "description": "Content ID from find_content results",
                },
                "media_type": {
                    "type": "string",
                    "description": "Media type from find_content results",
                    "enum": ["movie", "series", "episode", "season"],
                },
            },
            "required": ["channel_id", "content_id"],
        },
    ),
]

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


async def get_llm_provider() -> LLMProvider:
    """Get the configured LLM provider."""
    api_key = settings.llm_api_key
    if not api_key:
        raise ValueError("LLM API key is required. Set llm.api_key in config.yml")

    if settings.llm_provider == "anthropic":
        from ..core.llm.providers.anthropic import AnthropicProvider

        return AnthropicProvider(api_key=api_key, model=settings.llm_model)
    elif settings.llm_provider == "openai":
        from ..core.llm.providers.openai import OpenAIProvider

        return OpenAIProvider(api_key=api_key, model=settings.llm_model)
    else:
        raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")


async def get_tool_executor() -> ToolExecutor:
    """Get the configured tool executor."""
    if settings.tool_executor == "mcp":
        from ..core.llm.tool_executor import MCPToolExecutor

        # TODO: Initialize MCP client with proper configuration
        mcp_client = None  # Placeholder
        return MCPToolExecutor(mcp_client)
    elif settings.tool_executor == "roku_ecp":
        import httpx

        from ..core.brave_search import BraveSearchClient
        from ..core.llm.tool_executor import RokuECPToolExecutor

        http_client = httpx.AsyncClient()
        brave_client = None
        if settings.brave_api_key:
            brave_client = BraveSearchClient(
                api_key=settings.brave_api_key,
                http_client=http_client,
            )
        return RokuECPToolExecutor(settings.roku_ip, http_client, brave_client)
    else:
        raise ValueError(f"Unsupported tool executor: {settings.tool_executor}")


@router.websocket("/ws/chat")
async def websocket_chat(
    websocket: WebSocket,
    provider: LLMProvider = Depends(get_llm_provider),
    tool_executor: ToolExecutor = Depends(get_tool_executor),
):
    """WebSocket endpoint for chat with LLM."""
    await websocket.accept()
    logger.info("WebSocket connected")

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
                    tools=AVAILABLE_TOOLS,
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
async def roku_launch(channel_id: int, content_id: str, media_type: str = "movie"):
    """Direct Roku launch endpoint for frontend use.

    Proxies a launch request to the Roku ECP API.
    """
    import httpx

    from ..core.search_and_play import launch_on_roku

    roku_base_url = f"http://{settings.roku_ip}:8060"
    async with httpx.AsyncClient() as client:
        result = await launch_on_roku(
            channel_id=channel_id,
            content_id=content_id,
            roku_base_url=roku_base_url,
            http_client=client,
            media_type=media_type,
        )
    return {"success": result.success, "message": result.message}


@router.get("/chat/sessions")
async def list_sessions():
    """List all active chat sessions."""
    return {
        "sessions": session_store.list_sessions(),
        "count": len(session_store.list_sessions()),
    }


@router.delete("/chat/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a chat session."""
    session_store.delete_session(session_id)
    return {"message": f"Session {session_id} deleted"}


@router.post("/chat/sessions/{session_id}/reset")
async def reset_session(session_id: str):
    """Reset a chat session."""
    session = session_store.get_session(session_id)
    if session:
        session.reset()
        return {"message": f"Session {session_id} reset"}
    else:
        return {"error": f"Session {session_id} not found"}
