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
    # Row cap for a single result set. The marts are already aggregated
    # (monthly, per suburb/property-type), so legitimate time-series easily run
    # past a couple hundred rows — a 2-suburb monthly trend over 2010-2026 is
    # ~380 rows. 200 silently truncated those, cutting charts/compute_trend off
    # mid-series. The real runaway guard is sql_statement_timeout_ms below, not
    # this cap; keep it generous enough to hold a full aggregated series.
    max_rows: int = 5000

    # Bounded run_sql attempt budget (counts both failures and successes the
    # agent chooses to retry) and a hard statement timeout — the two guardrails
    # that make retries safe instead of just more expensive. See db.py/llm_agent.py.
    # 8, not 4: a real question spends 2-4 attempts just on discovery (does the
    # suburb exist / what's its casing / what date range) before the answer query
    # even runs. 4 let a single trend question exhaust the budget on probes and
    # never reach the real query. Cheap discovery now goes through lookup_values
    # (which does NOT count here); this budget is only for run_sql itself.
    max_sql_attempts: int = 8
    sql_statement_timeout_ms: int = 6000

    # Hard backstops on a single agent run, independent of model behaviour. A
    # misbehaving model (e.g. DeepSeek ignoring a "stop" tool return and looping
    # run_sql) once burned 731k tokens over 50 requests answering one question;
    # these cap the blast radius so no question can run away again. request_limit
    # is the primary guard (a healthy run is ~10-15 requests); the token cap is a
    # belt-and-braces ceiling. Both surface as UsageLimitExceeded, which the LLM
    # path salvages into a partial report from whatever real SQL already ran.
    agent_request_limit: int = 22
    agent_total_tokens_limit: int = 250_000

    # Cap how many knowledge pages one run may load. The playbook says "2-4
    # pages"; a run that read 9 pinned ~8k tokens of markdown into every
    # subsequent turn's context for no benefit. Past this, read_knowledge asks
    # the model to proceed with what it has.
    max_knowledge_reads: int = 6

    # Local embeddings for agent memory (recall/remember) — no API key needed.
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # Optional — ships agent traces to Logfire Cloud when set; local-only otherwise.
    logfire_token: str | None = None


settings = Settings()
