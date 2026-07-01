from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    # Agent connects as a strictly read-only role; RLS still applies.
    agent_database_url: str = "postgresql+asyncpg://agent_ro:agent_pw@db:5432/dataqa"
    db_ssl: str = ""  # set to "require" in Azure (managed Postgres needs TLS)

    # Provider is abstracted (Decision G): default Claude, stub when no key.
    anthropic_api_key: str | None = None
    model: str = "claude-sonnet-4-6"
    max_rows: int = 200


settings = Settings()
