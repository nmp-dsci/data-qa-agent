from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed app configuration. Reads env / .env; Key Vault in Azure (future)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    auth_mode: str = "dev"  # dev = local stub login; entra = real Entra External ID OIDC

    database_url: str = "postgresql+asyncpg://app_user:app_pw@db:5432/dataqa"
    db_ssl: str = ""  # set to e.g. "require" in Azure (managed Postgres needs TLS)
    agent_url: str = "http://data-agent:8100"

    # Dev-auth stub (auth_mode=dev): a locally signed HS256 token.
    jwt_secret: str = "dev-secret-change-me"
    jwt_alg: str = "HS256"
    jwt_ttl_seconds: int = 60 * 60 * 8

    # Microsoft Entra External ID (auth_mode=entra). All empty in dev; set per
    # deployment (Key Vault in Azure). The backend validates tokens against the
    # tenant's JWKS and never needs a client secret to do so.
    entra_authority: str = ""  # e.g. https://<tenant>.ciamlogin.com/<tenant-id>/v2.0
    entra_client_id: str = ""  # API app registration id (expected token audience)
    entra_audience: str = ""  # override if the API audience differs from client_id
    entra_openid_config_url: str = ""  # override the derived .well-known URL
    entra_admin_role: str = "admin"  # app-role value in the token that maps to admin

    @property
    def openid_config_url(self) -> str:
        if self.entra_openid_config_url:
            return self.entra_openid_config_url
        return f"{self.entra_authority.rstrip('/')}/.well-known/openid-configuration"

    @property
    def expected_audience(self) -> str:
        return self.entra_audience or self.entra_client_id

    cors_origins: list[str] = ["http://localhost:5230", "http://127.0.0.1:5230"]
    # Comma-separated extra origins injected per-deployment (e.g. the cloud frontend URL).
    extra_cors_origins: str = ""

    @property
    def all_cors_origins(self) -> list[str]:
        extra = [o.strip() for o in self.extra_cors_origins.split(",") if o.strip()]
        return self.cors_origins + extra


settings = Settings()
