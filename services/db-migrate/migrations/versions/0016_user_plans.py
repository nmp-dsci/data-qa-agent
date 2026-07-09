"""per-user app plans — free | plus | pro (s10 streaming pages)

Pages 2+ of an answer are gated per user (s10 decision D2): the data-agent's
page_plan(plan) plans one Summary page for free users, Summary + Insights for
plus/pro, and streams locked paywall-teaser entries for pages above the plan.
backend-api loads the plan with the current user and passes it to the agent on
every question, so the wire contract never needs to know about editions.

Seeds match the dev users: admin gets the maximum plan, user1 is free,
user2 is paid.

Revision ID: 0016_user_plans
Revises: 0015_template_studio
"""

from __future__ import annotations

from alembic import op

revision = "0016_user_plans"
down_revision = "0015_template_studio"
branch_labels = None
depends_on = None

SEED_PLANS = {"admin": "pro", "user1": "free", "user2": "plus"}


def upgrade() -> None:
    op.execute(
        "ALTER TABLE app.users ADD COLUMN IF NOT EXISTS plan text NOT NULL DEFAULT 'free' "
        "CHECK (plan IN ('free', 'plus', 'pro'))"
    )
    for username, plan in SEED_PLANS.items():
        op.execute(f"UPDATE app.users SET plan = '{plan}' WHERE username = '{username}'")

    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0016_user_plans') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.users DROP COLUMN IF EXISTS plan")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0016_user_plans'")
