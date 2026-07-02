"""query_runs token columns — agent metrics for the admin dashboard

Phase 3b. Captures pydantic-ai's run.usage() (input/output token counts) per
query so the admin dashboard can show real LLM usage, not just latency.
Nullable: the offline stub involves no LLM call, so its rows have no tokens.

Revision ID: 0004_query_run_tokens
Revises: 0003_agent_memory_grants
"""

from __future__ import annotations

from alembic import op

revision = "0004_query_run_tokens"
down_revision = "0003_agent_memory_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE app.query_runs "
        "ADD COLUMN input_tokens integer, "
        "ADD COLUMN output_tokens integer"
    )
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0004_query_run_tokens') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE app.query_runs DROP COLUMN input_tokens, DROP COLUMN output_tokens"
    )
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0004_query_run_tokens'")
