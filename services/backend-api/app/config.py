from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed app configuration. Reads env / .env; Key Vault in Azure (future)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    auth_mode: str = "dev"  # dev = local stub login; google = real Google OIDC sign-in

    database_url: str = "postgresql+asyncpg://app_user:app_pw@db:5432/dataqa"
    # Elevated read-only role (BYPASSRLS, SELECT-only; migration 0012). Used by
    # exactly one thing in this service: the ops rollup, which aggregates across
    # ALL users and therefore cannot run under any single user's RLS context.
    # Never reachable from a request handler.
    admin_ro_database_url: str = "postgresql+asyncpg://admin_ro:admin_pw@db:5432/dataqa"
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
    # Service accounts (s35) are shared by a whole surface — a Slack channel
    # behind one identity — so they get their own ceiling rather than a
    # role-based exemption. Still capped: a leaked key must not be able to run
    # up an unbounded bill. 0 disables the cap.
    ask_daily_limit_service: int = 200
    # Governed SELECTs per UTC day, service accounts only (s36). A raw query
    # costs no tokens, so human editor use stays uncapped — but a service key is
    # a credential handed to a machine that can loop, and the MCP surface hands
    # run_governed_query straight to a model. The guardrails apply to every
    # statement regardless; this is a rate bound, not a security control.
    sql_daily_limit_service: int = 500

    # ---- Integrations (s35) ------------------------------------------------
    # Slack signs its own requests; this is the workspace signing secret used to
    # verify X-Slack-Signature. Empty (the default) closes the Slack path
    # entirely, mirroring how ops_ingest_token gates the ingest endpoints.
    slack_signing_secret: str = ""

    # ---- MCP surface (s36) -------------------------------------------------
    # The SDK's DNS-rebinding host allowlist. Empty (the default) turns the check
    # OFF, which is deliberate: the surface is gated by a dpk_ service key, so
    # there is no anonymous path for a rebinding attack to reach, and requiring
    # an allowlist meant a deployment could not work until a second apply taught
    # it its own hostname. Set it to add defence in depth; the wildcard form is
    # "host:*" (the Host header includes the port), never a bare "*".
    mcp_allowed_hosts: str = ""
    mcp_allowed_origins: str = "*"

    @property
    def mcp_allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.mcp_allowed_hosts.split(",") if h.strip()]

    @property
    def mcp_allowed_origin_list(self) -> list[str]:
        return [o.strip() for o in self.mcp_allowed_origins.split(",") if o.strip()]

    # ---- Observability (s32 W2) --------------------------------------------
    # Optional — ships backend traces to Logfire Cloud when set; local-only
    # (console/no-op) otherwise, exactly like the data-agent's LOGFIRE_TOKEN.
    logfire_token: str | None = None
    # s37: self-hosted tracing. Logfire is an OpenTelemetry SDK, so the same
    # instrumentation can export anywhere — set this to an OTLP/HTTP collector
    # (locally the Jaeger container, http://jaeger:4318) and spans go there
    # instead of, or as well as, Logfire Cloud. Empty = off, which is the
    # default and matches the previous behaviour exactly.
    otlp_endpoint: str = ""

    # ---- Ops deck (s32 W0/W2/W4) ------------------------------------------
    # Machine token for POST /ops/ingest/* — the k6, promptfoo and deploy
    # writers, none of which have a user session or DB reachability. Empty (the
    # default) closes the path entirely; it is never open by accident.
    ops_ingest_token: str = ""
    # Tier-2 saturation: one CloudWatch GetMetricData pull per rollup refresh,
    # off the request path. Off by default — it needs boto3 plus an IAM read
    # grant on the App Runner instance role, and the deck renders Tier-1
    # telemetry alone without it.
    ops_cloudwatch_enabled: bool = False
    ops_cloudwatch_region: str = ""
    ops_cloudwatch_timeout_s: float = 8.0
    ops_apprunner_backend_service: str = ""
    ops_apprunner_agent_service: str = ""
    ops_apprunner_max_concurrency: int = 100
    ops_aurora_cluster_id: str = ""
    ops_cloudfront_distribution_id: str = ""
    # Shown beside 7d spend on the deck so cost has a scale, not just a number.
    ops_monthly_budget_usd: float = 50.0

    cors_origins: list[str] = ["http://localhost:5230", "http://127.0.0.1:5230"]
    # Comma-separated extra origins injected per-deployment (e.g. the cloud frontend URL).
    extra_cors_origins: str = ""

    @property
    def all_cors_origins(self) -> list[str]:
        extra = [o.strip() for o in self.extra_cors_origins.split(",") if o.strip()]
        return self.cors_origins + extra


settings = Settings()
