"""Basic tests for CLI components."""

from unittest.mock import MagicMock

import pytest

from cli.chat_client import ChatClient
from cli.config import Config
from cli.session_manager import SessionManager


def test_config():
    """Test CLI configuration."""
    cfg = Config()
    # Test default values
    assert cfg.backend_url.startswith("http")
    assert cfg.websocket_url.startswith("ws://")
    assert cfg.show_timestamps is True
    assert cfg.default_session_name == "cli-session"
    # Test computed property shape
    assert cfg.api_base_url.endswith("/chat")


def test_config_instance():
    """Test creating a new config instance."""
    cfg = Config(
        backend_url="http://test:9000",
        websocket_url="ws://test:9000/ws/chat",
    )
    assert cfg.backend_url == "http://test:9000"
    assert cfg.websocket_url == "ws://test:9000/ws/chat"
    assert cfg.api_base_url == "http://test:9000/chat"


@pytest.mark.asyncio
async def test_session_manager():
    """Test session manager basic functionality."""
    
    # Create session manager
    session_manager = SessionManager(Config())
    
    # Test initial state
    assert session_manager.current_session_id is None
    
    # Test creating session
    session_id = await session_manager.create_session()
    assert session_id is not None
    assert session_manager.current_session_id == session_id
    
    # Test switching to existing session
    existing_id = "test-session-123"
    await session_manager.create_session(existing_id)
    assert session_manager.current_session_id == existing_id
    
    # Test getting current session
    current_id = session_manager.get_current_session_id()
    assert current_id == existing_id


@pytest.mark.asyncio
async def test_chat_client():
    """Test chat client basic functionality."""
    # Mock console
    mock_console = MagicMock()
    
    # Create chat client
    chat_client = ChatClient(mock_console)
    
    # Test initial state
    assert not chat_client.is_connected
    assert chat_client.websocket is None
    
    # Test disconnect when not connected
    await chat_client.disconnect()
    assert not chat_client.is_connected


def test_mock_imports():
    """Test that all required modules can be imported."""
    # This test ensures our CLI structure is correct
    assert True
