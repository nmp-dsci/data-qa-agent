"""answer feedback + eval cases — element-anchored, versioned, triageable

The crux of the learning loop (§06/§07): users click a report element and leave
sentiment + a comment. Each capture is comprehensive — WHAT it judged (element
ref + a content snapshot), against WHICH agent (knowledge version), and HOW it is
used (scope: knowledge eval vs user memory; triage status). Admins batch-promote
captures into app.eval_cases, whose status is toggleable stale<->active in the UI
and auto-archives after 3 stale cycles. app.messages gains a `report` column so
the admin panel can re-render the exact report a user gave feedback on.

Revision ID: 0008_answer_feedback
Revises: 0007_query_run_trace
"""

from __future__ import annotations

from alembic import op

revision = "0008_answer_feedback"
down_revision = "0007_query_run_trace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Persist the structured report so the admin review panel can re-render it.
    op.execute("ALTER TABLE app.messages ADD COLUMN report jsonb")

    op.execute(
        """
        CREATE TABLE app.answer_feedback (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            message_id uuid NOT NULL REFERENCES app.messages(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES app.users(id),
            rating smallint NOT NULL CHECK (rating IN (-1, 1)),
            comment text,

            -- WHAT the feedback judged
            target_kind text NOT NULL,      -- report|headline|insight|profile|chart|query
            target_ref text NOT NULL,       -- element_id, e.g. 'insight:2'
            target_snapshot jsonb NOT NULL, -- element content AT feedback time

            -- WHICH agent produced it (staleness + attribution)
            knowledge_version text NOT NULL,
            knowledge_pages jsonb NOT NULL DEFAULT '[]'::jsonb,

            -- HOW it is used (triage lifecycle)
            scope text NOT NULL DEFAULT 'knowledge'
                CHECK (scope IN ('knowledge', 'user_memory')),
            status text NOT NULL DEFAULT 'new'
                CHECK (status IN ('new', 'promoted_to_eval', 'user_memory', 'dismissed')),

            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("ALTER TABLE app.answer_feedback ENABLE ROW LEVEL SECURITY")
    # Owner sees/writes their own; admins read all (admin bypass policy pattern
    # mirrors the other app tables — the admin role is not RLS-restricted here
    # because admin endpoints run under the admin's own user_id which owns nothing,
    # so add an explicit admin-read policy via role check on app.users.
    op.execute(
        """
        CREATE POLICY answer_feedback_owner ON app.answer_feedback
          USING (
            user_id = current_setting('app.current_user_id', true)::uuid
            OR EXISTS (
              SELECT 1 FROM app.users u
              WHERE u.id = current_setting('app.current_user_id', true)::uuid
                AND u.role = 'admin'
            )
          )
          WITH CHECK (
            user_id = current_setting('app.current_user_id', true)::uuid
            OR EXISTS (
              SELECT 1 FROM app.users u
              WHERE u.id = current_setting('app.current_user_id', true)::uuid
                AND u.role = 'admin'
            )
          )
        """
    )

    op.execute(
        """
        CREATE TABLE app.eval_cases (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            feedback_id uuid REFERENCES app.answer_feedback(id) ON DELETE SET NULL,
            question text NOT NULL,          -- re-asked on every eval run
            expectation text NOT NULL,       -- distilled from the comment
            target_kind text NOT NULL,
            target_snapshot jsonb NOT NULL,  -- what the feedback judged
            knowledge_version text NOT NULL,
            status text NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'stale', 'archived')),
            stale_cycles int NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # eval_cases is admin/CI-only curation data (no per-user rows) — like the
    # datasets registry, it is not row-level-secured; admin endpoints gate access.

    # Backend (app_user) reads/writes both new tables; the read-only agent has no
    # need for them.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON app.answer_feedback, app.eval_cases TO app_user"
    )

    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0008_answer_feedback') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.eval_cases")
    op.execute("DROP TABLE IF EXISTS app.answer_feedback")
    op.execute("ALTER TABLE app.messages DROP COLUMN IF EXISTS report")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0008_answer_feedback'")
