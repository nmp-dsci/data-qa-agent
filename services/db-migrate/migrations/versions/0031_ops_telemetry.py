"""ops telemetry — the tables the /ops flight deck reads (s32 W0)

One principle behind Track A: every hardening outcome becomes a row in Postgres,
and the Ops tab reads Postgres. This migration opens the store.

``app.query_runs`` gains the columns the deck's KPI band needs and the audit
never had: the prompt-cache token split (nominal ``input_tokens`` overstates
spend ~6x on this workload — see the cache-aware pricing module), the priced
``cost_usd``, whether the answer came back ``degraded`` (a retried-then-stubbed
answer rather than a 502), how many ``attempts`` it took, and the
``otel_trace_id`` that deep-links a slow row on the deck to its Logfire span
waterfall. ``status`` widens to admit 'degraded'.

Six new tables follow the established eval-tables convention (0019-0021):
created in the ``app`` schema, admin/CI-curated with no RLS, granted to
``app_user`` (the API writes them) and covered for ``admin_ro`` by the default
privileges 0012 already set. Live-traffic tables FK to ``app.query_runs(id)``
exactly as ``eval_results.query_run_id`` does.

``app.ops_rollup`` is the one that changes the read shape (decision Q3): the
summary endpoint reads pre-aggregated metrics per window instead of scanning
raw ``query_runs`` on every request, so the deck stays fast as the audit grows
past 3M rows. The column is ``window_key``, not ``window`` — ``window`` is a
reserved word in Postgres (the window-function clause) and would need quoting
at every call site.

Revision ID: 0031_ops_telemetry
Revises: 0030_eval_grader_spec
"""

from __future__ import annotations

from alembic import op

revision = "0031_ops_telemetry"
down_revision = "0030_eval_grader_spec"
branch_labels = None
depends_on = None

_QUERY_RUN_COLUMNS = (
    # The already-billed-cheap subset of input_tokens, and the write that
    # populated the cache. agent_common._build_trace already captures both per
    # model turn; 0031 promotes them out of the trace jsonb into columns so the
    # cost rollup is a sum, not a jsonb walk.
    ("cache_read_tokens", "integer"),
    ("cache_write_tokens", "integer"),
    # numeric, not float: a per-answer cost is fractions of a cent and summing
    # thousands of floats drifts.
    ("cost_usd", "numeric(12,6)"),
    ("degraded", "boolean NOT NULL DEFAULT false"),
    ("attempts", "integer"),
    ("otel_trace_id", "text"),
    # Time to the first streamed page — the *felt* latency, and the only thing
    # SLO-B can be measured against. Not in the plan's §6 column list, but the
    # SLO is explicitly "decoupled from extract-bound full-answer time", so it
    # needs its own measurement rather than a share of latency_ms. Null for
    # non-streaming paths (/ask, sql_editor, explore), which have no pages.
    ("ttfp_ms", "integer"),
)

_NEW_TABLES = (
    "app.load_tests",
    "app.security_runs",
    "app.deploy_events",
    "app.judge_samples",
    "app.pipeline_runs",
    "app.ops_rollup",
)


def upgrade() -> None:
    for name, ddl in _QUERY_RUN_COLUMNS:
        op.execute(f"ALTER TABLE app.query_runs ADD COLUMN IF NOT EXISTS {name} {ddl}")

    # The inline CHECK in db/init/01_schema.sql is unnamed, so Postgres called it
    # query_runs_status_check. Drop-and-recreate is the only way to widen it.
    op.execute("ALTER TABLE app.query_runs DROP CONSTRAINT IF EXISTS query_runs_status_check")
    op.execute(
        "ALTER TABLE app.query_runs ADD CONSTRAINT query_runs_status_check "
        "CHECK (status IN ('success', 'error', 'degraded'))"
    )
    # Every deck aggregate is windowed on created_at across all users; the
    # existing index leads with user_id, so it can't serve that scan.
    op.execute(
        "CREATE INDEX IF NOT EXISTS query_runs_created_at_idx ON app.query_runs (created_at DESC)"
    )

    # ---- W1: load-test results ------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app.load_tests (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL DEFAULT now(),
            scenario text NOT NULL DEFAULT '',
            vus integer,
            duration_s integer,
            rps numeric(10,3),
            p50_ms integer,
            p95_ms integer,
            p99_ms integer,
            error_rate numeric(6,4),
            git_sha text,
            notes text
        )
        """
    )

    # ---- W3: red-team / injection pack results --------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app.security_runs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL DEFAULT now(),
            kind text NOT NULL DEFAULT 'redteam'
                CHECK (kind IN ('redteam', 'injection')),
            pack_sha text,
            total integer NOT NULL DEFAULT 0,
            passed integer NOT NULL DEFAULT 0,
            by_category jsonb NOT NULL DEFAULT '{}'::jsonb,
            report_url text
        )
        """
    )

    # ---- W4: deploy telemetry -------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app.deploy_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            started_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            git_sha text NOT NULL DEFAULT '',
            actor text,
            status text NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'deployed', 'rolled_back', 'failed')),
            smoke jsonb NOT NULL DEFAULT '{}'::jsonb,
            duration_s integer,
            notes text
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS deploy_events_started_idx "
        "ON app.deploy_events (started_at DESC)"
    )

    # ---- W4: online judge sampling --------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app.judge_samples (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL DEFAULT now(),
            query_run_id uuid REFERENCES app.query_runs(id) ON DELETE CASCADE,
            judge_model text,
            rubric_hash text,
            insight_score numeric(5,2),
            verdict jsonb NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS judge_samples_created_idx "
        "ON app.judge_samples (created_at DESC)"
    )

    # ---- W2: pipeline / data freshness ----------------------------------
    # The exact failure class that took Explore down in prod (2026-07-21):
    # marts froze while the app kept shipping. Written by the pipeline job
    # itself, so the freshness panel needs no AWS call.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app.pipeline_runs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL DEFAULT now(),
            status text NOT NULL DEFAULT 'success'
                CHECK (status IN ('running', 'success', 'failed')),
            duration_s integer,
            marts_refreshed_at timestamptz,
            dbt_pass integer,
            dbt_total integer,
            row_counts jsonb NOT NULL DEFAULT '{}'::jsonb,
            git_sha text,
            source text,
            notes text
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS pipeline_runs_created_idx "
        "ON app.pipeline_runs (created_at DESC)"
    )

    # ---- W0: the pre-aggregated read surface (decision Q3) --------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app.ops_rollup (
            window_key text PRIMARY KEY,
            refreshed_at timestamptz NOT NULL DEFAULT now(),
            metrics jsonb NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(_NEW_TABLES)} TO app_user")
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0031_ops_telemetry') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    for table in _NEW_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP INDEX IF EXISTS app.query_runs_created_at_idx")
    op.execute("ALTER TABLE app.query_runs DROP CONSTRAINT IF EXISTS query_runs_status_check")
    # Restore the original two-value CHECK. Rows already stamped 'degraded'
    # would block it, so they are normalised to 'error' first — a downgrade
    # loses the distinction, which is the point of going back.
    op.execute("UPDATE app.query_runs SET status = 'error' WHERE status = 'degraded'")
    op.execute(
        "ALTER TABLE app.query_runs ADD CONSTRAINT query_runs_status_check "
        "CHECK (status IN ('success', 'error'))"
    )
    for name, _ddl in _QUERY_RUN_COLUMNS:
        op.execute(f"ALTER TABLE app.query_runs DROP COLUMN IF EXISTS {name}")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0031_ops_telemetry'")
