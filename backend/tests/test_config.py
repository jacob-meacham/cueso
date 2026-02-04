"""Tests for YAML configuration loading."""

from pathlib import Path
from unittest.mock import patch

from pydantic import SecretStr

from app.core.config import Settings

# Point CONFIG_FILE at a nonexistent path so only built-in defaults apply.
_NO_FILE = Path("/nonexistent/config.yaml")


class TestSettingsDefaults:
    def test_defaults_load(self) -> None:
        """Settings should have sensible defaults when no config file is present."""
        with patch("app.core.config.CONFIG_FILE", _NO_FILE):
            s = Settings()
            assert s.app.name == "Cueso Backend"
            assert s.app.debug is True
            assert s.llm.provider == "anthropic"

    def test_streaming_default(self) -> None:
        with patch("app.core.config.CONFIG_FILE", _NO_FILE):
            s = Settings()
            assert s.streaming == [
                "netflix",
                "hulu",
                "disney_plus",
                "max",
                "apple_tv_plus",
                "amazon_prime",
            ]

    def test_streaming_is_list_of_strings(self) -> None:
        with patch("app.core.config.CONFIG_FILE", _NO_FILE):
            s = Settings()
            assert isinstance(s.streaming, list)
            assert all(isinstance(name, str) for name in s.streaming)

    def test_nested_access(self) -> None:
        """Verify the nested config model structure works correctly."""
        with patch("app.core.config.CONFIG_FILE", _NO_FILE):
            s = Settings()
            assert s.app.name == "Cueso Backend"
            assert s.app.version == "0.1.0"
            assert s.app.environment == "development"
            assert s.app.debug is True
            assert s.app.hostname == "localhost"
            assert s.logging.level == "info"
            assert s.server.host == "0.0.0.0"
            assert s.server.port == 8483
            assert s.llm.provider == "anthropic"
            assert s.llm.model == "claude-3-5-sonnet-20241022"
            assert s.tools.executor == "roku_ecp"
            assert s.roku.ip == "192.168.1.100"
            assert s.mcp.server_url == ""


class TestSecretStrFields:
    def test_llm_api_key_is_secret(self) -> None:
        with patch("app.core.config.CONFIG_FILE", _NO_FILE):
            s = Settings(llm={"provider": "anthropic", "api_key": "sk-test-key"})  # type: ignore[arg-type]
            assert isinstance(s.llm.api_key, SecretStr)
            assert s.llm.api_key.get_secret_value() == "sk-test-key"
            # SecretStr should not leak in repr
            assert "sk-test-key" not in repr(s.llm.api_key)

    def test_brave_api_key_is_secret(self) -> None:
        with patch("app.core.config.CONFIG_FILE", _NO_FILE):
            s = Settings(brave={"api_key": "brave-key-123"})  # type: ignore[arg-type]
            assert isinstance(s.brave.api_key, SecretStr)
            assert s.brave.api_key.get_secret_value() == "brave-key-123"

    def test_mcp_server_token_is_secret(self) -> None:
        with patch("app.core.config.CONFIG_FILE", _NO_FILE):
            s = Settings(mcp={"server_url": "http://localhost", "server_token": "mcp-token"})  # type: ignore[arg-type]
            assert isinstance(s.mcp.server_token, SecretStr)
            assert s.mcp.server_token.get_secret_value() == "mcp-token"

    def test_secret_fields_default_to_none(self) -> None:
        with patch("app.core.config.CONFIG_FILE", _NO_FILE):
            s = Settings()
            assert s.llm.api_key is None
            assert s.brave.api_key is None
            assert s.mcp.server_token is None


class TestLogLevelNormalization:
    def test_uppercase(self) -> None:
        from app.core.config import LoggingConfig

        c = LoggingConfig(level="INFO")  # type: ignore[arg-type]
        assert c.level == "info"

    def test_warn_to_warning(self) -> None:
        from app.core.config import LoggingConfig

        c = LoggingConfig(level="warn")  # type: ignore[arg-type]
        assert c.level == "warning"
