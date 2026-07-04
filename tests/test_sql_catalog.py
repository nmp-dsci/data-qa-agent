from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-agent"))

from agent.schema import CURATED_CATALOG, get_catalog  # noqa: E402


def test_curated_catalog_has_core_tables() -> None:
    names = {f"{t['schema']}.{t['table']}" for t in CURATED_CATALOG}
    assert "marts.mart_sales_summary" in names
    assert "marts.mart_rent_summary" in names
    assert "marts.mart_rent_by_bedroom" in names
    assert "marts.mart_sales_by_segment" in names
    assert "marts.mart_property_yield" in names
    assert "staging.stg_sales" in names
    assert "staging.stg_rent" in names
    assert "staging.int_postcode_geo" in names
    assert "raw.sales" in names
    assert "raw.rent" in names


def test_catalog_entries_are_well_formed() -> None:
    catalog = get_catalog(role="admin")  # falls back to CURATED_CATALOG when DBT_MANIFEST unset
    assert catalog, "catalog must not be empty"
    for t in catalog:
        assert t["schema"] and t["table"]
        assert isinstance(t["columns"], list) and t["columns"]
        for col in t["columns"]:
            assert {"name", "type", "description"} <= set(col)
            assert col["name"]


def test_user_catalog_only_shows_marts_and_staging() -> None:
    catalog = get_catalog(role="user")
    assert catalog, "catalog must not be empty"
    assert {t["schema"] for t in catalog} <= {"marts", "staging"}
    assert "raw.sales" not in {f"{t['schema']}.{t['table']}" for t in catalog}


def test_admin_catalog_includes_raw_fallback_tables() -> None:
    catalog = get_catalog(role="admin")
    names = {f"{t['schema']}.{t['table']}" for t in catalog}
    assert "raw.sales" in names
    assert "raw.rent" in names


def test_admin_catalog_includes_app_tables() -> None:
    catalog = get_catalog(role="admin")
    names = {f"{t['schema']}.{t['table']}" for t in catalog}
    assert "app.users" in names
    assert "app.datasets" in names
    assert "app.dataset_access" in names
    assert "app.conversations" in names
    assert "app.messages" in names
    assert "app.query_runs" in names
    assert "app.user_memories" in names
    assert "app.events" in names


def test_non_admin_catalog_does_not_include_app_tables() -> None:
    catalog = get_catalog(role="user")
    assert "app.users" not in {f"{t['schema']}.{t['table']}" for t in catalog}
