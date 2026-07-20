"""nsw_yield — register the sales x rent yield dataset

s19 Explore, Phase A. The gross-yield mart (marts.property_yield) joins sales and
rent at postcode x property_type x month. Rather than require a user to hold both
the nsw_sales AND nsw_rent grants (cross-dataset semantics), yield is its own
dataset with its own grant. The mart itself is created by the data-pipeline (dbt)
and applies its own RLS via post-hook; this migration only registers the dataset
in the app registry and grants it to user1 (admin sees all via role).

Revision ID: 0025_nsw_yield_dataset
Revises: 0024_backfill_removed_templates
"""

from __future__ import annotations

from alembic import op

revision = "0025_nsw_yield_dataset"
down_revision = "0024_backfill_removed_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO app.datasets (slug, name, description, status) VALUES
          ('nsw_yield', 'NSW rental yield',
           'Gross rental yield (rent vs sale price) by postcode, property type and month'
           ' — sales joined to rent.',
           'ready')
        ON CONFLICT (slug) DO NOTHING
        """
    )

    # Grant to user1 (same holder as nsw_sales/nsw_rent; user2 excluded, admin sees all).
    op.execute(
        """
        INSERT INTO app.dataset_access (dataset_id, user_id, access)
        SELECT d.id, u.id, 'read'
        FROM app.datasets d, app.users u
        WHERE d.slug = 'nsw_yield' AND u.username = 'user1'
        ON CONFLICT (dataset_id, user_id) DO NOTHING
        """
    )

    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0025_nsw_yield_dataset') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM app.dataset_access WHERE dataset_id IN "
        "(SELECT id FROM app.datasets WHERE slug = 'nsw_yield')"
    )
    op.execute("DELETE FROM app.datasets WHERE slug = 'nsw_yield'")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0025_nsw_yield_dataset'")
