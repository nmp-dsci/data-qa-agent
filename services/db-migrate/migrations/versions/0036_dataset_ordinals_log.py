"""dataset_ordinals_log — append-only audit trail for curator ordinal edits (M3a)

``app.dataset_ordinals`` (0028) is the one model input with no version history:
a curator can silently redefine an axis order and any past agent run that used
the old order becomes unreproducible. This migration turns it into an
append-only log: a trigger on ``app.dataset_ordinals`` mirrors every INSERT/
UPDATE/DELETE into ``app.dataset_ordinals_log`` (id bigserial, occurred_at, op,
the affected ``(dataset_id, column_name)``, and the full old/new row as jsonb).
A backfill records day-one state as ``op = 'SEED'`` rows so a run made before
this migration still has *a* recorded input, even though it isn't a true delta.

The trigger function is SECURITY DEFINER (owned by the migration role, which
runs as the privileged ``postgres`` connection per ``env.py``) so it can write
to the log no matter which role performs the DML on the base table
(``app_user``, per 0028) — the log table itself grants no INSERT/UPDATE/DELETE
to any app role, so it is writable only via the trigger. Read access follows
0028's split: ``agent_ro`` (data-agent) and ``admin_ro`` (SQL editor) get
SELECT — ``admin_ro`` would already inherit it via 0012's default privileges,
but every neighbouring migration (0028, 0031, 0035) grants it explicitly, so
this one does too.

Revision ID: 0036_dataset_ordinals_log
Revises: 0035_promotions
"""

from __future__ import annotations

from alembic import op

revision = "0036_dataset_ordinals_log"
down_revision = "0035_promotions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.dataset_ordinals_log (
            id bigserial PRIMARY KEY,
            occurred_at timestamptz NOT NULL DEFAULT now(),
            op text NOT NULL CHECK (op IN ('INSERT', 'UPDATE', 'DELETE', 'SEED')),
            dataset_id uuid,
            column_name text,
            old_row jsonb,
            new_row jsonb
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS dataset_ordinals_log_dataset_col_idx "
        "ON app.dataset_ordinals_log (dataset_id, column_name, occurred_at DESC)"
    )

    # SECURITY DEFINER + a pinned search_path: the function must be able to
    # write to the log regardless of the invoking role's own grants, and a
    # fixed search_path keeps a SECURITY DEFINER function from being hijacked
    # by a session that sets search_path ahead of app.
    op.execute(
        """
        CREATE FUNCTION app.dataset_ordinals_log_fn() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = app, pg_temp AS $$
        BEGIN
            INSERT INTO app.dataset_ordinals_log (op, dataset_id, column_name, old_row, new_row)
            VALUES (
                TG_OP,
                COALESCE(NEW.dataset_id, OLD.dataset_id),
                COALESCE(NEW.column_name, OLD.column_name),
                CASE WHEN TG_OP IN ('UPDATE', 'DELETE') THEN to_jsonb(OLD) ELSE NULL END,
                CASE WHEN TG_OP IN ('INSERT', 'UPDATE') THEN to_jsonb(NEW) ELSE NULL END
            );
            RETURN COALESCE(NEW, OLD);
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER dataset_ordinals_log_trg "
        "AFTER INSERT OR UPDATE OR DELETE ON app.dataset_ordinals "
        "FOR EACH ROW EXECUTE FUNCTION app.dataset_ordinals_log_fn()"
    )

    # Backfill: record day-one state so a run made before this migration still
    # has a recorded input, even though it's not a true delta.
    op.execute(
        "INSERT INTO app.dataset_ordinals_log (op, dataset_id, column_name, old_row, new_row) "
        "SELECT 'SEED', dataset_id, column_name, NULL, to_jsonb(o) FROM app.dataset_ordinals o"
    )

    op.execute("GRANT SELECT ON app.dataset_ordinals_log TO agent_ro")
    op.execute("GRANT SELECT ON app.dataset_ordinals_log TO admin_ro")

    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0036_dataset_ordinals_log') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS dataset_ordinals_log_trg ON app.dataset_ordinals")
    op.execute("DROP FUNCTION IF EXISTS app.dataset_ordinals_log_fn()")
    op.execute("DROP TABLE IF EXISTS app.dataset_ordinals_log")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0036_dataset_ordinals_log'")
