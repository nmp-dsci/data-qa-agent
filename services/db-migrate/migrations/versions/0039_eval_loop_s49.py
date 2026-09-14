"""eval_loop_s49 — evidence density for the evaluation loop (s49)

Four things the s49 review found the loop could not answer, each a column:

* ``query_runs.artifact_manifest`` — the deck exactly as the agent specified
  it (every add_slide call: layout chosen, headline/commentary/kpi injected,
  frame + columns + chart type). It was only ever in the run result; now it is
  queryable, so the judge and the Evaluations tab see what was injected.
* ``eval_results.otel_trace_id`` / ``mlflow_run_id`` — the stored join from a
  graded case to its span waterfall and its MLflow case run. Before this you
  searched by time window.
* ``eval_results.judge`` / ``checkpoints`` / ``g5`` — the s49 judge verdict, the
  artifact grade that gates alongside G1 (never persisted before),
  ({label, diagnosis, ...}, advisory until the pack has 10 goldens) and the
  diagnostic checkpoint scores (sql / analysis / deck) that never gate (D1, D2).
* ``eval_cases.golden_answer`` / ``label`` / ``calibration_examples`` /
  ``checkpoints`` — golden v2: the reference answer the judge compares against,
  its ordinal label, curator-written calibration examples the judge must
  reproduce before it scores anything, and the hidden checkpoints.

Plus the curator write path for knowledge pages (D3): ``app.knowledge_pages``
overrides a file under services/data-agent/knowledge/ by path, with an
append-only log — the same shape 0036 gave ordinals. Pages are global (not
per-user) so there is no RLS; the data-agent reads them (agent_ro), the admin
SQL editor sees both tables (admin_ro), the app writes them (app_user).

Revision ID: 0039_eval_loop_s49
Revises: 0038_artifact_handover
"""

from __future__ import annotations

from alembic import op

revision = "0039_eval_loop_s49"
down_revision = "0038_artifact_handover"
branch_labels = None
depends_on = None

_QUERY_RUN_COLUMNS = (("artifact_manifest", "jsonb"),)
_EVAL_RESULT_COLUMNS = (
    ("otel_trace_id", "text"),
    ("mlflow_run_id", "text"),
    ("judge", "jsonb"),
    ("checkpoints", "jsonb"),
    ("g5", "jsonb"),
)
_EVAL_CASE_COLUMNS = (
    ("golden_answer", "text"),
    ("label", "text"),
    ("calibration_examples", "jsonb"),
    ("checkpoints", "jsonb"),
)


def upgrade() -> None:
    for name, ddl_type in _QUERY_RUN_COLUMNS:
        op.execute(f"ALTER TABLE app.query_runs ADD COLUMN IF NOT EXISTS {name} {ddl_type}")
    for name, ddl_type in _EVAL_RESULT_COLUMNS:
        op.execute(f"ALTER TABLE app.eval_results ADD COLUMN IF NOT EXISTS {name} {ddl_type}")
    for name, ddl_type in _EVAL_CASE_COLUMNS:
        op.execute(f"ALTER TABLE app.eval_cases ADD COLUMN IF NOT EXISTS {name} {ddl_type}")
    op.execute(
        "ALTER TABLE app.eval_cases ADD CONSTRAINT eval_cases_label_check "
        "CHECK (label IS NULL OR label IN ('low', 'medium', 'high')) NOT VALID"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app.knowledge_pages (
            path        text PRIMARY KEY,
            name        text NOT NULL,
            body        text NOT NULL,
            version     integer NOT NULL DEFAULT 1,
            author      text,
            updated_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app.knowledge_pages_log (
            id          bigserial PRIMARY KEY,
            path        text NOT NULL,
            version     integer NOT NULL,
            body        text NOT NULL,
            author      text,
            action      text NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS knowledge_pages_log_path_idx "
        "ON app.knowledge_pages_log (path, created_at DESC)"
    )

    op.execute("GRANT SELECT, INSERT, UPDATE ON app.knowledge_pages TO app_user")
    op.execute("GRANT SELECT, INSERT ON app.knowledge_pages_log TO app_user")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE app.knowledge_pages_log_id_seq TO app_user")
    op.execute("GRANT SELECT ON app.knowledge_pages TO agent_ro")
    op.execute("GRANT SELECT ON app.knowledge_pages, app.knowledge_pages_log TO admin_ro")
    op.execute("GRANT SELECT ON app.query_runs TO agent_ro")
    op.execute("GRANT SELECT ON app.query_runs TO admin_ro")

    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0039_eval_loop_s49') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("REVOKE ALL ON app.knowledge_pages, app.knowledge_pages_log FROM agent_ro, admin_ro")
    op.execute("REVOKE ALL ON app.knowledge_pages, app.knowledge_pages_log FROM app_user")
    op.execute("DROP TABLE IF EXISTS app.knowledge_pages_log")
    op.execute("DROP TABLE IF EXISTS app.knowledge_pages")
    op.execute("ALTER TABLE app.eval_cases DROP CONSTRAINT IF EXISTS eval_cases_label_check")
    for name, _ in _EVAL_CASE_COLUMNS:
        op.execute(f"ALTER TABLE app.eval_cases DROP COLUMN IF EXISTS {name}")
    for name, _ in _EVAL_RESULT_COLUMNS:
        op.execute(f"ALTER TABLE app.eval_results DROP COLUMN IF EXISTS {name}")
    for name, _ in _QUERY_RUN_COLUMNS:
        op.execute(f"ALTER TABLE app.query_runs DROP COLUMN IF EXISTS {name}")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0039_eval_loop_s49'")
