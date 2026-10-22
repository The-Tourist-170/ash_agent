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
    mock_repair_success_attempt: int = 1
    job_max_retries: int = 2
    graph_recursion_limit: int = 50

    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr = SecretStr("")
    llm_timeout_seconds: float = 60.0

    retrieval_enabled: bool = True
    workspace_dir: str = ".ash_agent/workspaces"
    git_clone_timeout_seconds: int = 120
    github_token: SecretStr = SecretStr("")
    github_base_url: str = "https://github.com"

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("ashagent")
    neo4j_database: str = "neo4j"

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr = SecretStr("")
    qdrant_collection: str = "code_chunks"

    embedding_provider: str = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dimension: int = 384
    embedding_cache_dir: str = ".ash_agent/models"

    retrieval_top_k: int = 8
    retrieval_chunk_lines: int = 60
    retrieval_chunk_overlap: int = 10
    retrieval_max_files: int = 500

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    @property
    def checkpoint_dsn(self) -> str:
        return self.database_url.replace("+asyncpg", "")

    @property
    def llm_configured(self) -> bool:
        return bool((self.llm_base_url or "").strip() and (self.llm_model or "").strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
