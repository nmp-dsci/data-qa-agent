from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed app configuration. Reads env / .env; Key Vault in Azure (future)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    auth_mode: str = "dev"  # dev = local stub login; google = real Google OIDC sign-in

    database_url: str = "postgresql+asyncpg://app_user:app_pw@db:5432/dataqa"
    db_ssl: str = ""  # set to e.g. "require" in Azure (managed Postgres needs TLS)
    agent_url: str = "http://data-agent:8100"
    # Shared token sent as X-Agent-Token on every agent call. Required by the
    # cloud agent (s12), whose App Runner URL is public. Empty = not sent (local).
    agent_shared_token: str = ""

    # Dev-auth stub (auth_mode=dev): a locally signed HS256 token.
    jwt_secret: str = "dev-secret-change-me"
    jwt_alg: str = "HS256"
    jwt_ttl_seconds: int = 60 * 60 * 8

    # Google Sign-in (auth_mode=google). Empty in dev; set per deployment
    # (Secrets Manager in AWS). The backend validates ID tokens against Google's
    # public JWKS and never needs the client secret to do so.
    google_client_id: str = ""  # OAuth 2.0 Web client id (expected token audience)
    # Comma-separated emails that map to the admin role; everyone else is a user.
    admin_emails: str = ""

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    # Per-user LLM cost caps by tier (s12 cheap hardening): max agent questions
    # per user per UTC day. The LLM is the dominant cost, so capping questions
    # caps spend. Paid = plan plus/pro; free = the rest; admins are uncapped.
    # 0 disables that tier's cap.
    ask_daily_limit_free: int = 5
    ask_daily_limit_paid: int = 10

    cors_origins: list[str] = ["http://localhost:5230", "http://127.0.0.1:5230"]
    # Comma-separated extra origins injected per-deployment (e.g. the cloud frontend URL).
    extra_cors_origins: str = ""

    @property
    def all_cors_origins(self) -> list[str]:
        extra = [o.strip() for o in self.extra_cors_origins.split(",") if o.strip()]
        return self.cors_origins + extra


settings = Settings()
