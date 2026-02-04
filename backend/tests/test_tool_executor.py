"""Tests for the tool executor system."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.brave_search import BraveSearchClient, BraveSearchError, SearchResult
from app.core.llm.tool_executor import MCPToolExecutor, RokuECPToolExecutor, ToolExecutor
from app.core.llm.types import ToolCall


def _tc(name: str, arguments: dict[str, object] | None = None) -> ToolCall:
    """Helper to create a ToolCall with a default id."""
    return ToolCall(id="test-id", name=name, arguments=dict(arguments) if arguments else {})


def test_tool_executor_abc() -> None:
    """Test that ToolExecutor is an abstract base class."""
    with pytest.raises(TypeError):
        ToolExecutor()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_mcp_tool_executor() -> None:
    """Test MCP tool executor."""
    # Mock MCP client
    mock_mcp_client = MagicMock()
    mock_mcp_client.call_tool = AsyncMock(return_value="MCP tool result")

    executor = MCPToolExecutor(mock_mcp_client)

    # Test successful execution
    tool_call = _tc("test_tool", {"arg1": "value1"})
    result = await executor.execute_tool(tool_call)

    assert result == "MCP tool result"
    mock_mcp_client.call_tool.assert_called_once_with(name="test_tool", arguments={"arg1": "value1"})


@pytest.mark.asyncio
async def test_mcp_tool_executor_error() -> None:
    """Test MCP tool executor error handling."""
    # Mock MCP client that raises an exception
    mock_mcp_client = MagicMock()
    mock_mcp_client.call_tool = AsyncMock(side_effect=Exception("MCP error"))

    executor = MCPToolExecutor(mock_mcp_client)

    # Test error handling
    tool_call = _tc("test_tool", {"arg1": "value1"})
    result = await executor.execute_tool(tool_call)

    assert "Error executing tool test_tool via MCP" in result
    assert "MCP error" in result


@pytest.mark.asyncio
async def test_roku_ecp_tool_executor() -> None:
    """Test Roku ECP tool executor."""
    # Mock HTTP client
    mock_http_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"model": "Roku Ultra"}
    mock_http_client.get = AsyncMock(return_value=mock_response)

    executor = RokuECPToolExecutor("192.168.1.100", mock_http_client)

    # Test search_roku tool — not yet implemented, returns error via outer handler
    tool_call = _tc("search_roku", {"query": "action movies", "channel": "Netflix"})
    result = await executor.execute_tool(tool_call)

    assert "Error executing tool search_roku" in result
    assert "not yet implemented" in result

    # Test get_roku_status tool
    tool_call = _tc("get_roku_status")
    result = await executor.execute_tool(tool_call)

    assert "Roku device is online" in result
    assert "Roku Ultra" in result


@pytest.mark.asyncio
async def test_roku_ecp_tool_executor_unknown_tool() -> None:
    """Test Roku ECP tool executor with unknown tool."""
    mock_http_client = MagicMock()
    executor = RokuECPToolExecutor("192.168.1.100", mock_http_client)

    # Test unknown tool
    tool_call = _tc("unknown_tool")
    result = await executor.execute_tool(tool_call)

    assert result == "Unknown tool: unknown_tool"


@pytest.mark.asyncio
async def test_roku_ecp_tool_executor_error() -> None:
    """Test Roku ECP tool executor error handling."""
    # Mock HTTP client that raises an exception
    mock_http_client = MagicMock()
    mock_http_client.get = AsyncMock(side_effect=Exception("HTTP error"))

    executor = RokuECPToolExecutor("192.168.1.100", mock_http_client)

    # get_roku_status now propagates errors to outer handler
    tool_call = _tc("get_roku_status")
    result = await executor.execute_tool(tool_call)

    assert "Error executing tool get_roku_status" in result
    assert "HTTP error" in result

    # search_roku raises NotImplementedError, caught by outer handler
    tool_call = _tc("search_roku", {"query": "test"})
    result = await executor.execute_tool(tool_call)

    assert "Error executing tool search_roku" in result
    assert "not yet implemented" in result


@pytest.mark.asyncio
async def test_roku_ecp_tool_executor_http_error() -> None:
    """Test Roku ECP tool executor with HTTP error response."""
    # Mock HTTP client with error response
    mock_http_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_http_client.get = AsyncMock(return_value=mock_response)

    executor = RokuECPToolExecutor("192.168.1.100", mock_http_client)

    # Test HTTP error handling
    tool_call = _tc("get_roku_status")
    result = await executor.execute_tool(tool_call)

    assert "Roku device returned status 500" in result


def test_roku_ecp_tool_executor_base_url() -> None:
    """Test Roku ECP tool executor base URL construction."""
    mock_http_client = MagicMock()
    executor = RokuECPToolExecutor("192.168.1.100", mock_http_client)

    assert executor.base_url == "http://192.168.1.100:8060"
    assert executor.roku_ip == "192.168.1.100"


# --- web_search tool tests ---


@pytest.mark.asyncio
async def test_web_search_tool_success() -> None:
    """Test web_search tool returns formatted results."""
    mock_http_client = MagicMock()
    mock_brave_client = AsyncMock(spec=BraveSearchClient)
    mock_brave_client.search.return_value = [
        SearchResult(title="Result 1", url="https://example.com/1", description="First"),
        SearchResult(title="Result 2", url="https://example.com/2", description="Second"),
    ]

    executor = RokuECPToolExecutor("192.168.1.100", mock_http_client, mock_brave_client)
    tool_call = _tc("web_search", {"query": "Rick and Morty snake episode", "count": 2})
    result = await executor.execute_tool(tool_call)

    assert "Result 1" in result
    assert "Result 2" in result
    assert "https://example.com/1" in result
    mock_brave_client.search.assert_called_once_with("Rick and Morty snake episode", count=2)


@pytest.mark.asyncio
async def test_web_search_tool_no_brave_client() -> None:
    """Test web_search returns error when brave_client is not configured."""
    mock_http_client = MagicMock()
    executor = RokuECPToolExecutor("192.168.1.100", mock_http_client, brave_client=None)

    tool_call = _tc("web_search", {"query": "test"})
    result = await executor.execute_tool(tool_call)

    assert "not configured" in result
    assert "BRAVE_API_KEY" in result


@pytest.mark.asyncio
async def test_web_search_tool_no_results() -> None:
    """Test web_search returns message when no results found."""
    mock_http_client = MagicMock()
    mock_brave_client = AsyncMock(spec=BraveSearchClient)
    mock_brave_client.search.return_value = []

    executor = RokuECPToolExecutor("192.168.1.100", mock_http_client, mock_brave_client)
    tool_call = _tc("web_search", {"query": "nonexistent thing"})
    result = await executor.execute_tool(tool_call)

    assert "No results found" in result


@pytest.mark.asyncio
async def test_web_search_tool_search_error() -> None:
    """Test web_search handles BraveSearchError."""
    mock_http_client = MagicMock()
    mock_brave_client = AsyncMock(spec=BraveSearchClient)
    mock_brave_client.search.side_effect = BraveSearchError("Rate limited")

    executor = RokuECPToolExecutor("192.168.1.100", mock_http_client, mock_brave_client)
    tool_call = _tc("web_search", {"query": "test"})
    result = await executor.execute_tool(tool_call)

    assert "Search error" in result


# --- find_content tool tests ---


@pytest.mark.asyncio
async def test_find_content_tool_success() -> None:
    """Test find_content returns JSON with matches."""
    import json

    mock_http_client = MagicMock()
    mock_brave_client = AsyncMock(spec=BraveSearchClient)
    mock_brave_client.search.return_value = [
        SearchResult(title="Show | Netflix", url="https://www.netflix.com/title/12345", description=""),
    ]

    executor = RokuECPToolExecutor("192.168.1.100", mock_http_client, mock_brave_client)
    tool_call = _tc("find_content", {"title": "Show"})
    result = await executor.execute_tool(tool_call)

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert len(parsed["matches"]) == 1
    assert parsed["matches"][0]["service_name"] == "netflix"
    assert parsed["matches"][0]["channel_id"] == 12


@pytest.mark.asyncio
async def test_find_content_tool_no_brave() -> None:
    """Test find_content returns error when brave not configured."""
    import json

    mock_http_client = MagicMock()
    executor = RokuECPToolExecutor("192.168.1.100", mock_http_client, brave_client=None)

    tool_call = _tc("find_content", {"title": "Show"})
    result = await executor.execute_tool(tool_call)

    parsed = json.loads(result)
    assert parsed["success"] is False
    assert "not configured" in parsed["message"]


# --- launch_on_roku tool tests ---


@pytest.mark.asyncio
async def test_launch_on_roku_tool_success() -> None:
    """Test launch_on_roku calls Roku ECP and returns result."""
    import json

    mock_http_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_http_client.post = AsyncMock(return_value=mock_response)

    executor = RokuECPToolExecutor("192.168.1.100", mock_http_client)
    tool_call = _tc("launch_on_roku", {"channel_id": 12, "content_id": "81234567", "media_type": "movie"})
    result = await executor.execute_tool(tool_call)

    parsed = json.loads(result)
    assert parsed["success"] is True
    mock_http_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_launch_on_roku_tool_missing_params() -> None:
    """Test launch_on_roku returns error for missing params."""
    import json

    mock_http_client = MagicMock()
    executor = RokuECPToolExecutor("192.168.1.100", mock_http_client)

    tool_call = _tc("launch_on_roku")
    result = await executor.execute_tool(tool_call)

    parsed = json.loads(result)
    assert parsed["success"] is False
    assert "required" in parsed["message"]
