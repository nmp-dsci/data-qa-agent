"""property market — drop toy housing, register nsw_sales + nsw_rent

Phase 2b. Retires the toy `housing` dataset (its mart is now built by dbt) and
registers the two real datasets. The growth marts themselves are created by the
data-pipeline (dlt + dbt); their RLS is applied by dbt post-hooks. Access:
user1 gets both datasets, user2 gets neither (admin sees all) — preserving the
isolation demo.

Revision ID: 0002_property_market
Revises: 0001_phase0_init
"""

from __future__ import annotations

from alembic import op

revision = "0002_property_market"
down_revision = "0001_phase0_init"
branch_labels = None
depends_on = None

NEW_DATASETS = ("nsw_sales", "nsw_rent")


def upgrade() -> None:
    # The toy housing objects are superseded by the dbt-built property marts.
    op.execute("DROP TABLE IF EXISTS marts.housing CASCADE")
    op.execute("DROP TABLE IF EXISTS raw.housing CASCADE")

    # Reseed the dataset registry.
    op.execute("DELETE FROM app.datasets WHERE slug = 'housing'")
    op.execute(
        """
        INSERT INTO app.datasets (slug, name, description, status) VALUES
          ('nsw_sales', 'NSW property sales',
           'NSW Government residential sale prices — sale-price growth by suburb.', 'ready'),
          ('nsw_rent', 'NSW rental bonds',
           'NSW Rental Bond Board lodgements — weekly-rent growth by suburb/postcode.', 'ready')
        ON CONFLICT (slug) DO NOTHING
        """
    )

    # Grant both datasets to user1 (admin sees all via role; user2 excluded).
    op.execute(
        """
        INSERT INTO app.dataset_access (dataset_id, user_id, access)
        SELECT d.id, u.id, 'read'
        FROM app.datasets d, app.users u
        WHERE d.slug IN ('nsw_sales', 'nsw_rent') AND u.username = 'user1'
        ON CONFLICT (dataset_id, user_id) DO NOTHING
        """
    )

    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0002_property_market') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM app.dataset_access WHERE dataset_id IN "
        "(SELECT id FROM app.datasets WHERE slug IN ('nsw_sales', 'nsw_rent'))"
    )
    op.execute("DELETE FROM app.datasets WHERE slug IN ('nsw_sales', 'nsw_rent')")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0002_property_market'")
