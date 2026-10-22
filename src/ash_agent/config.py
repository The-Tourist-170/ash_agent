from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "ash-agent"
    environment: str = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://ash:ash@localhost:5432/ash_agent"
    valkey_url: str = "redis://localhost:6379/0"
    rq_queue_name: str = "ash_agent:jobs"
    run_job_timeout_seconds: int = 3600

    github_webhook_secret: SecretStr = SecretStr("")
    max_attempts: int = 3

    api_host: str = "0.0.0.0"
    api_port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
