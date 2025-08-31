from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # App Configuration
    app_name: str = "Cueso"
    app_version: str = "0.1.0"
    debug: bool = Field(default=False, alias="DEBUG")

    # Server Configuration
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")

    # Roku Configuration
    roku_ip: str = Field(default="192.168.1.100", alias="ROKU_IP")

    # LLM Configuration
    llm_provider: Literal["openai", "anthropic", "local"] = Field(
        default="openai", alias="LLM_PROVIDER"
    )
    llm_model: str = Field(default="gpt-4", alias="LLM_MODEL")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Environment Configuration
    environment: str = Field(default="development", alias="ENVIRONMENT")
    hostname: str = Field(default="localhost", alias="HOSTNAME")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }

# Global settings instance
settings = Settings()
