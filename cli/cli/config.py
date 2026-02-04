"""Unified CLI configuration.

Single source of truth that merges:
- sensible defaults
- persisted config file at ~/.cueso/config.json
- optional overrides passed in at construction

This avoids separate managers/wrappers and provides a small, ergonomic API.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_BACKEND = "http://localhost:8181"


@dataclass(init=False)
class Config:
    # Core URLs
    backend_url: str
    websocket_url: str

    # Display
    show_timestamps: bool

    # Session
    default_session_name: str

    # Internal
    _config_dir: Path = field(default_factory=lambda: Path.home() / ".cueso", repr=False)
    _config_file: Path = field(init=False, repr=False)

    def __init__(
        self,
        *,
        backend_url: str | None = None,
        websocket_url: str | None = None,
        show_timestamps: bool | None = None,
        default_session_name: str | None = None,
    ) -> None:
        # Defaults
        self.backend_url = DEFAULT_BACKEND
        self.websocket_url = ""  # derived from backend_url below
        self.show_timestamps = True
        self.default_session_name = "cli-session"

        # Internal paths
        self._config_dir = Path.home() / ".cueso"
        self._config_file = self._config_dir / "config.json"

        # Merge file
        file_data = self.load_file()
        if file_data:
            self.backend_url = file_data.get("backend_url", self.backend_url)
            self.websocket_url = file_data.get("websocket_url", self.websocket_url)
            self.show_timestamps = file_data.get("show_timestamps", self.show_timestamps)
            self.default_session_name = file_data.get("default_session_name", self.default_session_name)

        # Apply overrides from constructor
        if backend_url is not None:
            self.backend_url = backend_url
        if websocket_url is not None:
            self.websocket_url = websocket_url
        if show_timestamps is not None:
            self.show_timestamps = show_timestamps
        if default_session_name is not None:
            self.default_session_name = default_session_name

        # If websocket not explicitly provided, derive from backend
        if websocket_url is None:
            host = self.backend_url.replace("http://", "").replace("https://", "")
            self.websocket_url = f"ws://{host}/ws/chat"

    # Derived
    @property
    def api_base_url(self) -> str:
        return f"{self.backend_url}/chat"

    # Persistence
    def load_file(self) -> dict[str, Any]:
        if not self._config_file.exists():
            return {}
        try:
            with self._config_file.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_file(self) -> None:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        with self._config_file.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "backend_url": self.backend_url,
                    "websocket_url": self.websocket_url,
                    "show_timestamps": self.show_timestamps,
                    "default_session_name": self.default_session_name,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        try:
            self._config_dir.chmod(0o700)
            self._config_file.chmod(0o600)
        except Exception:
            pass

    def to_dict(self) -> dict[str, Any]:
        """Return current config as a dict."""
        return {
            "backend_url": self.backend_url,
            "websocket_url": self.websocket_url,
            "show_timestamps": self.show_timestamps,
            "default_session_name": self.default_session_name,
        }

    def set_value(self, key: str, value: Any) -> None:
        """Set a supported config key and persist to disk."""
        if key == "backend_url":
            self.backend_url = str(value)
            # keep websocket derived unless explicitly set elsewhere
            host = self.backend_url.replace("http://", "").replace("https://", "")
            self.websocket_url = f"ws://{host}/ws/chat"
        elif key == "websocket_url":
            self.websocket_url = str(value)
        elif key == "show_timestamps":
            self.show_timestamps = bool(value)
        elif key == "default_session_name":
            self.default_session_name = str(value)
        else:
            # ignore unknown keys silently
            return

        self.save_file()

    # Merge API removed; use constructor parameters instead

    # Small ergonomic helpers
    def get_backend_url(self) -> str:
        return self.backend_url

    def get_websocket_url(self) -> str:
        return self.websocket_url

    def get_api_base_url(self) -> str:
        return self.api_base_url

