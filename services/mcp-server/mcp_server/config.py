from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed config for the MCP server. Same .env conventions as the other services."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # This server holds NO database credentials by design. It is an MCP-protocol
    # adapter in front of backend-api's HTTP surface, so the SQL guardrails, RLS
    # scoping, daily cap and audit trail stay in exactly one place and cannot
    # drift between two codebases.
    backend_url: str = "http://backend-api:8000"

    # The server's own dpk_ service key (surface='mcp'). Everything this server
    # can see is whatever that key is granted — there is no ambient authority.
    # Empty means unconfigured, and every tool says so rather than half-working.
    mcp_service_key: str = ""

    request_timeout_s: float = 180.0

    host: str = "0.0.0.0"  # noqa: S104 - containerised; the port is what's published
    port: int = 8200

    # The MCP SDK ships DNS-rebinding protection: it refuses any request whose
    # Host header isn't declared here, which is right, and which silently 421s
    # every request if you get it wrong. Two things worth knowing:
    #   * the Host header includes the PORT, so bare "localhost" does not match
    #     "localhost:8200" — the wildcard form is "localhost:*", not "*"
    #   * a deployment must add its own hostname (App Runner's *.awsapprunner.com
    #     domain), because there is deliberately no allow-everything value
    # Defaults cover local compose and a laptop; anything else declares itself.
    allowed_hosts: str = "localhost:*,127.0.0.1:*,mcp-server:*,localhost,127.0.0.1,mcp-server"
    allowed_origins: str = "*"

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def allowed_origin_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


settings = Settings()
