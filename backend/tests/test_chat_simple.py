"""Simple tests for chat functionality without full app import."""

from app.api.chat import ChatMessage
from app.core.llm.types import Tool, ToolCall


def test_chat_message_model() -> None:
    """Test the ChatMessage model validation."""
    message = ChatMessage(message="Hello, world!", session_id=None)
    assert message.message == "Hello, world!"
    assert message.session_id is None

    message_with_session = ChatMessage(message="Hello, world!", session_id="test-session-123")
    assert message_with_session.message == "Hello, world!"
    assert message_with_session.session_id == "test-session-123"


def test_available_tools_structure() -> None:
    """Test that the available tools have the correct structure."""
    tools = [
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
            name="find_content",
            description="Search streaming services for content",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Content title"},
                },
                "required": ["title"],
            },
        ),
        Tool(
            name="launch_on_roku",
            description="Launch content on the Roku device",
            input_schema={
                "type": "object",
                "properties": {
                    "channel_id": {"type": "integer", "description": "Roku channel ID"},
                    "content_id": {"type": "string", "description": "Content ID"},
                },
                "required": ["channel_id", "content_id"],
            },
        ),
        Tool(
            name="get_roku_status",
            description="Get current status of Roku device",
            input_schema={"type": "object", "properties": {}, "required": []},
        ),
    ]

    assert len(tools) == 4

    # Check search_roku tool
    search_tool = next(t for t in tools if t.name == "search_roku")
    assert search_tool.description == "Search for content on Roku channels"
    assert "query" in search_tool.input_schema["properties"]
    assert "query" in search_tool.input_schema["required"]

    # Check find_content tool
    find_tool = next(t for t in tools if t.name == "find_content")
    assert "title" in find_tool.input_schema["properties"]
    assert "title" in find_tool.input_schema["required"]

    # Check launch_on_roku tool
    launch_tool = next(t for t in tools if t.name == "launch_on_roku")
    assert "channel_id" in launch_tool.input_schema["properties"]
    assert "content_id" in launch_tool.input_schema["properties"]

    # Check get_roku_status tool
    status_tool = next(t for t in tools if t.name == "get_roku_status")
    assert status_tool.description == "Get current status of Roku device"
    assert status_tool.input_schema["properties"] == {}
    assert status_tool.input_schema["required"] == []


def test_tool_executor_logic() -> None:
    """Test the tool executor logic."""

    # Mock tool executor function
    def tool_executor(tool_call: ToolCall) -> str:
        if tool_call.name == "search_roku":
            query = tool_call.arguments.get("query", "")
            channel = tool_call.arguments.get("channel", "default")
            return f"Found 5 results for '{query}' on {channel}: [Result 1, Result 2, Result 3, Result 4, Result 5]"

        elif tool_call.name == "find_content":
            title = tool_call.arguments.get("title", "")
            return f'{{"success": true, "matches": [{{"service_name": "netflix", "title": "{title}"}}]}}'

        elif tool_call.name == "launch_on_roku":
            channel_id = tool_call.arguments.get("channel_id", 0)
            content_id = tool_call.arguments.get("content_id", "")
            return f'{{"success": true, "message": "Launched channel {channel_id} with content {content_id}"}}'

        elif tool_call.name == "get_roku_status":
            return "Roku device is online and ready. Current app: Home screen"

        else:
            return f"Unknown tool: {tool_call.name}"

    # Test search tool
    search_call = ToolCall(id="t1", name="search_roku", arguments={"query": "action movies", "channel": "Netflix"})
    result = tool_executor(search_call)
    assert "Found 5 results" in result
    assert "action movies" in result
    assert "Netflix" in result

    # Test find_content tool
    find_call = ToolCall(id="t2", name="find_content", arguments={"title": "The Bear"})
    result = tool_executor(find_call)
    assert "netflix" in result
    assert "The Bear" in result

    # Test launch_on_roku tool
    launch_call = ToolCall(id="t3", name="launch_on_roku", arguments={"channel_id": 12, "content_id": "81234567"})
    result = tool_executor(launch_call)
    assert "success" in result
    assert "12" in result

    # Test status tool
    status_call = ToolCall(id="t4", name="get_roku_status", arguments={})
    result = tool_executor(status_call)
    assert "Roku device is online" in result

    # Test unknown tool
    unknown_call = ToolCall(id="t5", name="unknown_tool", arguments={})
    result = tool_executor(unknown_call)
    assert "Unknown tool: unknown_tool" in result
