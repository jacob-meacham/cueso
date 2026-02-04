"""Application configuration and settings, loaded from config.yml."""

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

# Bootstrap: config file path from env var or default.
# Default is config.yml relative to backend/ working directory.
CONFIG_FILE = Path(os.environ.get("CUESO_CONFIG", "config.yml"))


# --- Nested config models ---


class AppConfig(BaseModel):
    """App-level settings."""

    name: str = "Cueso Backend"
    version: str = "0.1.0"
    environment: str = "development"
    debug: bool = True
    hostname: str = "localhost"
    allowed_origins: list[str] = Field(default_factory=list)


class LoggingConfig(BaseModel):
    """Logging settings."""

    level: Literal["debug", "info", "warning", "error", "critical"] = "info"

    @field_validator("level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> str:
        if value is None:
            return "info"
        if isinstance(value, str):
            level = value.strip().lower()
            if level == "warn":
                level = "warning"
            return level
        if isinstance(value, int):
            if value <= 10:
                return "debug"
            if value <= 20:
                return "info"
            if value <= 30:
                return "warning"
            if value <= 40:
                return "error"
            return "critical"
        return str(value).strip().lower()


class ServerConfig(BaseModel):
    """Server settings."""

    host: str = "0.0.0.0"
    port: int = 8483


class LLMConfig(BaseModel):
    """LLM provider settings."""

    provider: str = "anthropic"
    api_key: SecretStr | None = None
    model: str = "claude-3-5-sonnet-20241022"


class ToolsConfig(BaseModel):
    """Tool execution settings."""

    executor: str = "roku_ecp"


class RokuConfig(BaseModel):
    """Roku device settings."""

    ip: str = "192.168.1.100"


class MCPConfig(BaseModel):
    """MCP settings."""

    server_url: str = ""
    server_token: SecretStr | None = None


class BraveConfig(BaseModel):
    """Brave Search settings."""

    api_key: SecretStr | None = None


# --- Top-level settings ---


class Settings(BaseSettings):
    """Application settings loaded from config.yml with env var overrides."""

    app: AppConfig = Field(default_factory=AppConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    roku: RokuConfig = Field(default_factory=RokuConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    brave: BraveConfig = Field(default_factory=BraveConfig)
    streaming: list[str] = Field(
        default_factory=lambda: [
            "netflix",
            "hulu",
            "disney_plus",
            "max",
            "apple_tv_plus",
            "amazon_prime",
        ]
    )

    model_config = {
        "extra": "ignore",
        "env_nested_delimiter": "__",
    }

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        from pydantic_settings import YamlConfigSettingsSource

        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=CONFIG_FILE),
        )


# Global settings instance
settings = Settings()
