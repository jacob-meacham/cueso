"""Tool execution interface and implementations."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from ..brave_search import BraveSearchError
from ..search_and_play import launch_on_roku, search_content
from .types import ROKU_ECP_PORT, Tool

if TYPE_CHECKING:
    from ..brave_search import BraveSearchClient
    from .types import ToolCall


class ToolExecutor(ABC):
    """Abstract base class for tool execution strategies."""

    @abstractmethod
    async def execute_tool(self, tool_call: ToolCall) -> str:
        """Execute a tool call and return the result."""
        pass


class MCPToolExecutor(ToolExecutor):
    """Tool executor that uses MCP (Model Context Protocol) for external tool calls."""

    def __init__(self, mcp_client: Any) -> None:
        self.mcp_client = mcp_client

    async def execute_tool(self, tool_call: ToolCall) -> str:
        """Execute tool via MCP server."""
        try:
            # Use MCP client to call the tool
            result = await self.mcp_client.call_tool(name=tool_call.name, arguments=tool_call.arguments)
            return str(result)
        except Exception as e:
            return f"Error executing tool {tool_call.name} via MCP: {e}"


# --- Tool definitions co-located with their handler implementations ---

TOOL_DEFINITIONS: list[Tool] = [
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


class RokuECPToolExecutor(ToolExecutor):
    """Tool executor that directly calls Roku ECP APIs."""

    def __init__(
        self,
        roku_ip: str,
        http_client: Any,
        brave_client: BraveSearchClient | None = None,
    ) -> None:
        self.roku_ip = roku_ip
        self.http_client = http_client
        self.base_url = f"http://{roku_ip}:{ROKU_ECP_PORT}"
        self.brave_client = brave_client
        self._handlers: dict[str, Callable[..., Awaitable[str]]] = {
            "search_roku": self._search_roku,
            "get_roku_status": self._get_roku_status,
            "web_search": self._web_search,
            "find_content": self._find_content,
            "launch_on_roku": self._launch_on_roku,
        }

    @classmethod
    def get_tool_definitions(cls) -> list[Tool]:
        """Return the tool definitions co-located with this executor."""
        return TOOL_DEFINITIONS

    async def execute_tool(self, tool_call: ToolCall) -> str:
        """Execute tool by dispatching to the registered handler."""
        try:
            handler = self._handlers.get(tool_call.name)
            if handler is None:
                return f"Unknown tool: {tool_call.name}"
            return await handler(tool_call.arguments)
        except Exception as e:
            return f"Error executing tool {tool_call.name}: {e}"

    async def _search_roku(self, arguments: dict[str, Any]) -> str:
        """Search for content on Roku channels."""
        raise NotImplementedError("search_roku is not yet implemented against the Roku ECP API")

    async def _get_roku_status(self, arguments: dict[str, Any]) -> str:
        """Get current status of Roku device."""
        response = await self.http_client.get(f"{self.base_url}/query/device-info")
        if response.status_code == 200:
            device_info = response.json()
            return f"Roku device is online. Model: {device_info.get('model', 'Unknown')}"
        else:
            return f"Roku device returned status {response.status_code}"

    async def _web_search(self, arguments: dict[str, Any]) -> str:
        """General web search via Brave Search API."""
        if self.brave_client is None:
            return "Error: Brave Search is not configured. Set BRAVE_API_KEY."

        query = arguments.get("query", "")
        count = arguments.get("count", 5)
        try:
            results = await self.brave_client.search(query, count=count)
            if not results:
                return f"No results found for: {query}"
            formatted: list[str] = []
            for i, r in enumerate(results, 1):
                formatted.append(f"{i}. {r.title}\n   URL: {r.url}\n   {r.description}")
            return "\n\n".join(formatted)
        except BraveSearchError as e:
            return f"Search error: {e}"

    async def _find_content(self, arguments: dict[str, Any]) -> str:
        """Search streaming services for content, returning all matches."""
        if self.brave_client is None:
            return json.dumps({"success": False, "message": "Brave Search is not configured.", "matches": []})

        result = await search_content(
            title=arguments.get("title", ""),
            brave_client=self.brave_client,
            season=arguments.get("season"),
            episode=arguments.get("episode"),
            episode_title=arguments.get("episode_title"),
            media_type=arguments.get("media_type"),
        )
        return result.to_tool_result()

    async def _launch_on_roku(self, arguments: dict[str, Any]) -> str:
        """Launch content on Roku given channel_id and content_id."""
        channel_id = arguments.get("channel_id")
        content_id = arguments.get("content_id")
        media_type = arguments.get("media_type", "movie")

        if not channel_id or not content_id:
            return json.dumps({"success": False, "message": "channel_id and content_id are required."})

        result = await launch_on_roku(
            channel_id=int(channel_id),
            content_id=str(content_id),
            roku_base_url=self.base_url,
            http_client=self.http_client,
            media_type=media_type,
        )
        return json.dumps({"success": result.success, "message": result.message})
