"""dataset_ordinals — curator-editable ordinal band order per dataset (s23)

Some dimension columns are ordinal (area_band, bedroom_band) but stored as
strings that sort alphabetically wrong. ``app.dataset_ordinals`` holds the
canonical order per ``(dataset, column)`` so charts render bands in their natural
order. The data-agent reads it (agent_ro) before lifting a chart; the admin
Golden tab edits it (app_user). Global admin config — like app.agent_config it is
not row-level-secured; the admin endpoint gates writes. Seeds the two known
columns idempotently from app.datasets (a no-op if a dataset row is absent). The
code seed in agent/ordinals.py remains the fallback if this table is empty.

Revision ID: 0028_dataset_ordinals
Revises: 0027_publish_table_chart
"""

from __future__ import annotations

from alembic import op

revision = "0028_dataset_ordinals"
down_revision = "0027_publish_table_chart"
branch_labels = None
depends_on = None

# (dataset slug, column, ordered values) — same as agent/ordinals.py::BAND_ORDERS.
SEED = [
    ("nsw_sales", "area_band", '["<400","400-700","700-1000","1000-5000","5000+","unknown"]'),
    ("nsw_rent", "bedroom_band", '["0","1","2","3","4","5+","unknown"]'),
]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.dataset_ordinals (
            dataset_id  uuid NOT NULL REFERENCES app.datasets(id) ON DELETE CASCADE,
            column_name text NOT NULL,
            ordered_values jsonb NOT NULL DEFAULT '[]'::jsonb,
            updated_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (dataset_id, column_name)
        )
        """
    )
    # Backend-api (app_user) reads + writes; the data-agent (agent_ro) reads to
    # order the axis. admin_ro already gets SELECT via 0012 default privileges.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON app.dataset_ordinals TO app_user")
    op.execute("GRANT SELECT ON app.dataset_ordinals TO agent_ro")

    for slug, column, values in SEED:
        op.execute(
            "INSERT INTO app.dataset_ordinals (dataset_id, column_name, ordered_values) "
            f"SELECT id, '{column}', '{values}'::jsonb FROM app.datasets WHERE slug = '{slug}' "
            "ON CONFLICT (dataset_id, column_name) DO NOTHING"
        )

    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0028_dataset_ordinals') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.dataset_ordinals")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0028_dataset_ordinals'")
