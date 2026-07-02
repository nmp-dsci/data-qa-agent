from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    # Agent connects as a strictly read-only role; RLS still applies.
    agent_database_url: str = "postgresql+asyncpg://agent_ro:agent_pw@db:5432/dataqa"
    db_ssl: str = ""  # set to "require" in Azure (managed Postgres needs TLS)

    # Provider is abstracted (Decision G): pluggable LLM, stub when no key.
    # LLM_PROVIDER picks which key is used; no cross-provider fallback (see provider.py).
    llm_provider: str = "deepseek"
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-chat"
    anthropic_api_key: str | None = None
    model: str = "claude-sonnet-4-6"
    max_rows: int = 200

    # Local embeddings for agent memory (recall/remember) — no API key needed.
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # Optional — ships agent traces to Logfire Cloud when set; local-only otherwise.
    logfire_token: str | None = None


settings = Settings()
