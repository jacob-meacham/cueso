"""Tests for YAML configuration loading."""

from pathlib import Path
from unittest.mock import patch

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


class TestBackwardCompatProperties:
    def test_app_properties(self) -> None:
        s = Settings()
        assert s.app_name == s.app.name
        assert s.app_version == s.app.version
        assert s.environment == s.app.environment
        assert s.debug == s.app.debug
        assert s.hostname == s.app.hostname

    def test_server_properties(self) -> None:
        s = Settings()
        assert s.host == s.server.host
        assert s.port == s.server.port

    def test_llm_properties(self) -> None:
        s = Settings()
        assert s.llm_provider == s.llm.provider
        assert s.llm_api_key == s.llm.api_key
        assert s.llm_model == s.llm.model

    def test_other_properties(self) -> None:
        s = Settings()
        assert s.log_level == s.logging.level
        assert s.tool_executor == s.tools.executor
        assert s.roku_ip == s.roku.ip
        assert s.mcp_server_url == s.mcp.server_url
        assert s.mcp_server_token == s.mcp.server_token
        assert s.brave_api_key == s.brave.api_key


class TestLogLevelNormalization:
    def test_uppercase(self) -> None:
        from app.core.config import LoggingConfig

        c = LoggingConfig(level="INFO")  # type: ignore[arg-type]
        assert c.level == "info"

    def test_warn_to_warning(self) -> None:
        from app.core.config import LoggingConfig

        c = LoggingConfig(level="warn")  # type: ignore[arg-type]
        assert c.level == "warning"
