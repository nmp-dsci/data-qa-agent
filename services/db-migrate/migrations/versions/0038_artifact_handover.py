"""artifact_handover — the change log for decks/sheets after they leave the app (s46/s48 §7)

A deck is handed to the user as a Google Slides file; from that moment the app
has no visibility into it unless something looks. This adds the two tables and
three ``query_runs`` columns the poller (``scripts/handover_poll.py``) needs to
answer "did anyone open this, and what did they change": a normalised snapshot
of deck/sheet content per Drive ``version`` seen, and a diff-derived edit log
against the previous snapshot.

Both tables key off ``query_runs.id`` (cascade on delete — an artifact's
history is meaningless once the run that produced it is gone) rather than the
Drive file id directly, because a run's deck/sheet ids already live on
``query_runs`` (0037) and every other handover query starts from "which runs
have artifacts", not "which files exist".

Read access: a snapshot carries the deck's headlines and the sheet's cell
values, so both tables carry RLS policies scoped through ``query_runs`` to the
run's owner (admin override, as on ``query_runs`` itself). The SQL editor runs
as ``agent_ro``, which does not bypass RLS, so one user cannot read another's
deck content through it.

Write access: the poller runs unattended in the data-agent service, touching
runs across every user, so it needs the same RLS bypass the admin SQL editor
uses (``admin_ro``, 0012) — but that role is deliberately SELECT-only
everywhere else (an admin using the SQL editor must never be able to write).
Rather than loosen that blanket grant, or add a third database role for one
background job, this migration grants ``admin_ro`` INSERT on the two new
tables and UPDATE on exactly the three new ``query_runs`` columns — nothing
else on ``query_runs`` becomes writable through that role.

Revision ID: 0038_artifact_handover
Revises: 0037_run_artifacts
"""

from __future__ import annotations

from alembic import op

revision = "0038_artifact_handover"
down_revision = "0037_run_artifacts"
branch_labels = None
depends_on = None

_QUERY_RUN_COLUMNS = (
    ("artifact_last_checked", "timestamptz"),
    ("artifact_opened_at", "timestamptz"),
    ("artifact_edit_count", "integer not null default 0"),
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.artifact_snapshots (
            id uuid primary key default gen_random_uuid(),
            run_id uuid not null references app.query_runs(id) on delete cascade,
            kind text not null check (kind in ('deck','sheet')),
            file_id text not null,
            drive_version bigint not null,
            modified_time timestamptz not null,
            snapshot jsonb not null,
            taken_at timestamptz not null default now(),
            unique (run_id, kind, drive_version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE app.artifact_edits (
            id bigserial primary key,
            run_id uuid not null references app.query_runs(id) on delete cascade,
            kind text not null,
            event text not null,
            layout_id text,
            slide_index int,
            slide_object_id text,
            table_name text,
            before jsonb,
            after jsonb,
            actor text,
            drive_version bigint not null,
            observed_at timestamptz not null default now()
        )
        """
    )
    op.execute("CREATE INDEX ON app.artifact_edits (run_id, observed_at)")

    for name, ddl_type in _QUERY_RUN_COLUMNS:
        op.execute(f"ALTER TABLE app.query_runs ADD COLUMN IF NOT EXISTS {name} {ddl_type}")

    # RLS. Both tables hold per-user content (a snapshot carries the deck's
    # headlines and the sheet's cell values), so they are policed like every
    # other user-scoped app.* table rather than left open behind the SELECT
    # grant below — without this, any signed-in user could read every other
    # user's deck content through the SQL editor, which runs as agent_ro.
    #
    # Neither table has its own user_id: ownership lives on the run, so the
    # policy reaches through query_runs. The WHOLE predicate is wrapped in
    # (select ...) deliberately — a bare app.is_admin() in an RLS predicate is
    # re-evaluated per row (STABLE does not help); see the Explore regression
    # in AGENTS.md. admin_ro is BYPASSRLS, so the poller and /analytics/handover
    # are unaffected.
    for table in ("artifact_snapshots", "artifact_edits"):
        op.execute(f"ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_owner ON app.{table} FOR ALL USING ((SELECT "
            "app.is_admin() OR EXISTS (SELECT 1 FROM app.query_runs qr "
            f"WHERE qr.id = app.{table}.run_id AND qr.user_id = app.current_user_id())))"
        )

    # Reads: both new tables follow the same access pattern as every other
    # app.* table (agent_ro for the service, admin_ro for the SQL editor/ops).
    for role in ("agent_ro", "admin_ro"):
        op.execute(f"GRANT SELECT ON app.artifact_snapshots TO {role}")
        op.execute(f"GRANT SELECT ON app.artifact_edits TO {role}")
    op.execute("GRANT SELECT ON app.query_runs TO agent_ro")
    op.execute("GRANT SELECT ON app.query_runs TO admin_ro")

    # Writes: the poller only, via admin_ro — see the module docstring for why
    # this is scoped rather than a blanket grant.
    op.execute("GRANT INSERT ON app.artifact_snapshots TO admin_ro")
    op.execute("GRANT INSERT ON app.artifact_edits TO admin_ro")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE app.artifact_edits_id_seq TO admin_ro")
    op.execute(
        "GRANT UPDATE (artifact_last_checked, artifact_opened_at, artifact_edit_count) "
        "ON app.query_runs TO admin_ro"
    )

    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0038_artifact_handover') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("REVOKE ALL ON app.artifact_snapshots FROM agent_ro, admin_ro")
    op.execute("REVOKE ALL ON app.artifact_edits FROM agent_ro, admin_ro")
    op.execute(
        "REVOKE UPDATE (artifact_last_checked, artifact_opened_at, artifact_edit_count) "
        "ON app.query_runs FROM admin_ro"
    )
    for table in ("artifact_snapshots", "artifact_edits"):
        op.execute(f"DROP POLICY IF EXISTS {table}_owner ON app.{table}")
    op.execute("DROP TABLE IF EXISTS app.artifact_edits")
    op.execute("DROP TABLE IF EXISTS app.artifact_snapshots")
    for name, _ in _QUERY_RUN_COLUMNS:
        op.execute(f"ALTER TABLE app.query_runs DROP COLUMN IF EXISTS {name}")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0038_artifact_handover'")
