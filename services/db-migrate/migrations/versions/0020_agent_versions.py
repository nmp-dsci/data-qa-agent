"""agent_versions registry + query_runs stamp (s14 E2)

A first-class descriptor of *which agent build* produced a run — the composed
behaviour fingerprint (D5): provider + model_id + system-prompt hash + skills
hash + knowledge_version + container image/git_sha, hashed once at boot. Every
/ask (chat or eval) is stamped via query_runs.agent_version_id, so a
base-vs-proposed comparison is exact and a diff pinpoints which lever moved.

Admin/CI-curated data (like eval_cases) — not row-level-secured; the backend
(app_user) upserts a row per distinct fingerprint and stamps the run.

Revision ID: 0020_agent_versions
Revises: 0019_eval_goldens
"""

from __future__ import annotations

from alembic import op

revision = "0020_agent_versions"
down_revision = "0019_eval_goldens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app.agent_versions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            fingerprint text NOT NULL UNIQUE,
            label text NOT NULL DEFAULT '',
            provider text NOT NULL DEFAULT '',
            model_id text NOT NULL DEFAULT '',
            prompt_hash text NOT NULL DEFAULT '',
            skills_hash text NOT NULL DEFAULT '',
            knowledge_version text NOT NULL DEFAULT '',
            image_tag text NOT NULL DEFAULT '',
            git_sha text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("GRANT SELECT, INSERT ON app.agent_versions TO app_user")
    op.execute(
        "ALTER TABLE app.query_runs "
        "ADD COLUMN IF NOT EXISTS agent_version_id uuid REFERENCES app.agent_versions(id)"
    )
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0020_agent_versions') "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.query_runs DROP COLUMN IF EXISTS agent_version_id")
    op.execute("DROP TABLE IF EXISTS app.agent_versions")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0020_agent_versions'")
