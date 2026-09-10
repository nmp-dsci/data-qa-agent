"""run_artifacts — the Sheets/Slides artifact a run produced (s46)

An answer is no longer a page the browser draws; it is a Google Slides deck
backed by a Google Sheet. Those live outside this database, so a run needs to
record *where* — otherwise reopening history, replaying a demo pack entry, or
grading a golden has no way back to what the user actually received.

Two text columns rather than one jsonb blob, deliberately: ``otel_trace_id``
(0031) set the precedent for "one deep link per run as its own column", and the
deck and the sheet are separately useful (the deck is what you show, the sheet
is what you re-derive from). The per-slide manifest that the graders diff stays
in the run's trace, where the rest of the run's structure already lives.

Both are nullable: every run before this migration has none, a run made with
DECK_EXPORT off has none, and that is a normal state rather than a defect.

Revision ID: 0037_run_artifacts
Revises: 0036_dataset_ordinals_log
"""

from __future__ import annotations

from alembic import op

revision = "0037_run_artifacts"
down_revision = "0036_dataset_ordinals_log"
branch_labels = None
depends_on = None

_QUERY_RUN_COLUMNS = (
    ("artifact_deck_url", "text"),
    ("artifact_sheet_url", "text"),
)


def upgrade() -> None:
    for name, ddl_type in _QUERY_RUN_COLUMNS:
        op.execute(f"ALTER TABLE app.query_runs ADD COLUMN IF NOT EXISTS {name} {ddl_type}")

    # Read access mirrors every other query_runs column: the data-agent role and
    # the admin SQL editor both already hold SELECT on the table, and 0012's
    # default privileges cover new columns, but neighbouring migrations grant
    # explicitly rather than relying on that — so this one does too.
    op.execute("GRANT SELECT ON app.query_runs TO agent_ro")
    op.execute("GRANT SELECT ON app.query_runs TO admin_ro")

    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0037_run_artifacts') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    for name, _ in _QUERY_RUN_COLUMNS:
        op.execute(f"ALTER TABLE app.query_runs DROP COLUMN IF EXISTS {name}")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0037_run_artifacts'")
