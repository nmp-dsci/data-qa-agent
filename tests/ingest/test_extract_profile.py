from __future__ import annotations

from data_qa_agent.ingest.extract_profile import PROFILE_COLUMNS, extract_profile

from .conftest import FakeYFClient


def test_profile_columns_and_currency(aapl_info):
    client = FakeYFClient(info=aapl_info)
    df, currency = extract_profile(client, "AAPL", "202606041415")
    assert list(df.columns) == PROFILE_COLUMNS
    assert len(df) == 1
    row = df.iloc[0]
    assert row["ticker"] == "AAPL"
    assert row["company_name"] == "Apple Inc."
    assert row["sector"] == "Technology"
    assert row["currency"] == "USD"
    assert row["ingested_at"] == "2026-06-04T14:15:00+00:00"
    assert currency == "USD"


def test_profile_reuses_passed_info(aapl_info):
    client = FakeYFClient(info=aapl_info)
    extract_profile(client, "AAPL", "202606041415", info=aapl_info)
    assert client.info_calls == 0  # did not fetch again


def test_profile_falls_back_to_shortname():
    client = FakeYFClient(info={"shortName": "Foo", "currency": "USD"})
    df, _ = extract_profile(client, "FOO", "202606041415")
    assert df.iloc[0]["company_name"] == "Foo"
