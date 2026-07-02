"""agent memory grants — agent_ro can read/write its own memory table

Phase 3. app.user_memories was created in the 0001 baseline (RLS already
scoped owner-only), but agent_ro (the role data-agent connects as) was never
granted access to it, so recall()/remember() would fail with a permission
error. SELECT + INSERT only — nothing in this phase updates an existing row
(e.g. last_used_at); add UPDATE in a future migration if that changes.

Revision ID: 0003_agent_memory_grants
Revises: 0002_property_market
"""

from __future__ import annotations

from alembic import op

revision = "0003_agent_memory_grants"
down_revision = "0002_property_market"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT, INSERT ON app.user_memories TO agent_ro")
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0003_agent_memory_grants') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT ON app.user_memories FROM agent_ro")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0003_agent_memory_grants'")
