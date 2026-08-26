"""queue telemetry — queue_wait_ms / worker_id / deliveries on query_runs (s40 M1)

The queue seam splits an answer's latency into queue wait + service time, and
the write-up needs both per run: queue_wait_ms is enqueue -> worker pickup (as
measured by the worker), worker_id says which replica answered (consumer-
$HOSTNAME), deliveries counts redeliveries (1 = first attempt; >1 means a
worker died mid-job and XAUTOCLAIM handed it on — M2). All NULL on the direct
(QUEUE_MODE=off) path, which is how a run's path is told apart in analysis.

Revision ID: 0034_queue_telemetry
Revises: 0033_demo_user
"""

from __future__ import annotations

from alembic import op

revision = "0034_queue_telemetry"
down_revision = "0033_demo_user"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.query_runs ADD COLUMN IF NOT EXISTS queue_wait_ms integer")
    op.execute("ALTER TABLE app.query_runs ADD COLUMN IF NOT EXISTS worker_id text")
    op.execute("ALTER TABLE app.query_runs ADD COLUMN IF NOT EXISTS deliveries integer")
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0034_queue_telemetry') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.query_runs DROP COLUMN IF EXISTS queue_wait_ms")
    op.execute("ALTER TABLE app.query_runs DROP COLUMN IF EXISTS worker_id")
    op.execute("ALTER TABLE app.query_runs DROP COLUMN IF EXISTS deliveries")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0034_queue_telemetry'")
