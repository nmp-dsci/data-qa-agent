from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    # Agent + regular SQL-editor users connect as a strictly read-only role; RLS
    # still applies (rows scoped to the user's datasets / own operational rows).
    agent_database_url: str = "postgresql+asyncpg://agent_ro:agent_pw@db:5432/dataqa"
    # Elevated read-only role for the admin SQL editor: BYPASSRLS + SELECT on every
    # schema, so an admin can query any table (incl. internal app.* tables) and see
    # all rows. Only role == "admin" requests route here; still SELECT-only. Created
    # by migration 0012_admin_ro_role.
    admin_ro_database_url: str = "postgresql+asyncpg://admin_ro:admin_pw@db:5432/dataqa"
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
    # that make retries safe instead of just more expensive. See db.py.
    # 8, not 4: a real question spends 2-4 attempts just on discovery (does the
    # suburb exist / what's its casing / what date range) before the answer query
    # even runs. 4 let a single trend question exhaust the budget on probes and
    # never reach the real query. Cheap discovery now goes through lookup_values
    # (which does NOT count here); this budget is only for run_sql itself.
    max_sql_attempts: int = 8
    sql_statement_timeout_ms: int = 6000

    # Total run_analysis attempts per question. Confirmed retry budget is 2, i.e.
    # 1 initial run + 2 self-corrections = 3. Skills do the risky maths (tested
    # once), so a run rarely needs all three.
    sandbox_run_attempts: int = 3

    # Which executor runs run_analysis code (restructure Phase A vs B):
    #   "subprocess" — the quick restricted-builtins spawned process (Phase A).
    #     Zero extra deps; NOT a hard isolation boundary against a determined
    #     escape. The default so host unit tests run without Node.
    #   "pyodide"    — the hardened Pyodide/WASM runtime (Phase B): model code runs
    #     in WebAssembly with no syscalls, no host filesystem, no network. Needs
    #     Node + the bundled pyodide in the image; docker-compose sets it on.
    # Both share the same run_code() signature, skills, and AnalysisResult, so the
    # agent surface is identical — this only swaps the isolation boundary.
    sandbox_runtime: str = "subprocess"

    # Hard backstops on a single agent run, independent of model behaviour. A
    # misbehaving model (e.g. DeepSeek ignoring a "stop" tool return and looping
    # run_sql) once burned 731k tokens over 50 requests answering one question;
    # these cap the blast radius so no question can run away again. request_limit
    # is the primary guard (a healthy run is ~10-15 requests); the token cap is a
    # belt-and-braces ceiling. Both surface as UsageLimitExceeded, which the LLM
    # path salvages into a partial report from whatever real SQL already ran.
    #
    # Phase 0 stopgap (data-agent restructure): raised 250k → 600k. The heavy
    # two-dataset question legitimately tips ~252k nominal tokens and was falling
    # to the stub, but nominal tokens are ~6x cache-inflated (billed_full ≈ 1/6th),
    # so the real cost of the higher ceiling is small. request_limit stays the
    # primary runaway guard; this just stops a genuine two-dataset report from
    # being cut off. The sandbox+skills restructure makes the ceiling moot later.
    agent_request_limit: int = 22
    agent_total_tokens_limit: int = 600_000

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
