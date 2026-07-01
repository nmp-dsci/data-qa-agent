from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed app configuration. Reads env / .env; Key Vault in Azure (future)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    auth_mode: str = "dev"  # dev = local stub login; entra = real OIDC (future)

    database_url: str = "postgresql+asyncpg://app_user:app_pw@db:5432/dataqa"
    db_ssl: str = ""  # set to e.g. "require" in Azure (managed Postgres needs TLS)
    agent_url: str = "http://data-agent:8100"

    jwt_secret: str = "dev-secret-change-me"
    jwt_alg: str = "HS256"
    jwt_ttl_seconds: int = 60 * 60 * 8

    cors_origins: list[str] = ["http://localhost:5230", "http://127.0.0.1:5230"]
    # Comma-separated extra origins injected per-deployment (e.g. the cloud frontend URL).
    extra_cors_origins: str = ""

    @property
    def all_cors_origins(self) -> list[str]:
        extra = [o.strip() for o in self.extra_cors_origins.split(",") if o.strip()]
        return self.cors_origins + extra


settings = Settings()
