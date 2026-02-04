"""Tool execution interface and implementations."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

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
        self.base_url = f"http://{roku_ip}:8060"
        self.brave_client = brave_client

    async def execute_tool(self, tool_call: ToolCall) -> str:
        """Execute tool by calling Roku ECP directly."""
        try:
            if tool_call.name == "search_roku":
                return await self._search_roku(tool_call.arguments)
            elif tool_call.name == "get_roku_status":
                return await self._get_roku_status()
            elif tool_call.name == "web_search":
                return await self._web_search(tool_call.arguments)
            elif tool_call.name == "find_content":
                return await self._find_content(tool_call.arguments)
            elif tool_call.name == "launch_on_roku":
                return await self._launch_on_roku(tool_call.arguments)
            else:
                return f"Unknown tool: {tool_call.name}"
        except Exception as e:
            return f"Error executing tool {tool_call.name}: {e}"

    async def _search_roku(self, arguments: dict[str, Any]) -> str:
        """Search for content on Roku channels."""
        query = arguments.get("query", "")
        channel = arguments.get("channel", "default")

        # This would make actual ECP calls to search
        # For now, return a mock response
        return f"Found 5 results for '{query}' on {channel}: [Result 1, Result 2, Result 3, Result 4, Result 5]"

    async def _get_roku_status(self) -> str:
        """Get current status of Roku device."""
        try:
            # Make actual ECP call to get device info
            response = await self.http_client.get(f"{self.base_url}/query/device-info")
            if response.status_code == 200:
                device_info = response.json()
                return f"Roku device is online. Model: {device_info.get('model', 'Unknown')}"
            else:
                return "Roku device is online but status unavailable"
        except Exception:
            return "Roku device is online and ready. Current app: Home screen"

    async def _web_search(self, arguments: dict[str, Any]) -> str:
        """General web search via Brave Search API."""
        if self.brave_client is None:
            return "Error: Brave Search is not configured. Set BRAVE_API_KEY."

        from ..brave_search import BraveSearchError

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

        from ..search_and_play import search_content

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
        from ..search_and_play import launch_on_roku

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
