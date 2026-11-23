from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    dev_api_key: str = "change-me"
    database_url: str = "postgresql://merchant_ai:merchant_ai@localhost:5432/merchant_ai"
    upload_dir: Path = Path("./data/uploads")

    ai_provider: str = "local"
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_chat_model: str = ""
    ai_embedding_model: str = ""
    ai_embedding_dimensions: int = Field(default=1024, ge=32, le=4096)

    auto_reply_prefix: str = "【AI客服】"
    min_retrieval_score: float = Field(default=0.25, ge=0, le=1)
    agent_command_lease_seconds: int = Field(default=30, ge=5, le=300)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    return settings

