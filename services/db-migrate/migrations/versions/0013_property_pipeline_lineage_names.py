"""property pipeline lineage names

Drop superseded raw/staging/mart relation names before the dbt pipeline creates
the lineage-friendly property_sales/property_rent tables in each layer.

Revision ID: 0013_property_lineage_names
Revises: 0012_admin_ro_role
"""

from __future__ import annotations

from alembic import op

revision = "0013_property_lineage_names"
down_revision = "0012_admin_ro_role"
branch_labels = None
depends_on = None


OLD_RELATIONS = (
    "marts.mart_property_yield",
    "marts.mart_rent_by_bedroom",
    "marts.mart_rent_summary",
    "marts.mart_sales_by_segment",
    "marts.mart_sales_summary",
    "marts.property_yield",
    "marts.rent_by_bedroom",
    "marts.rent_summary",
    "marts.sales_by_segment",
    "marts.sales_summary",
    "staging.stg_sales",
    "staging.stg_rent",
    "raw.sales",
    "raw.rent",
)


def upgrade() -> None:
    for relation in OLD_RELATIONS:
        op.execute(f"DROP TABLE IF EXISTS {relation} CASCADE")
    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES "
        "('0013_property_pipeline_lineage_names') ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM app.schema_migrations WHERE version = '0013_property_pipeline_lineage_names'"
    )
