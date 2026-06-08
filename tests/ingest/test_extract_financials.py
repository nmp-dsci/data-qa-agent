from __future__ import annotations

import numpy as np

from data_qa_agent.ingest.extract_financials import FINANCIALS_COLUMNS, extract_financials
from data_qa_agent.ingest.models import Dataset, DatasetState

from .conftest import FakeYFClient, make_statement

LOAD_ID = "202606041415"


def _client_with_balance():
    annual = make_statement(
        ["2025-09-30", "2024-09-30"],
        {"TotalAssets": [364000.0, 352000.0], "TotalDebt": [np.nan, 100.0]},
    )
    quarterly = make_statement(
        ["2026-03-31"],
        {"TotalAssets": [370000.0]},
    )
    return FakeYFClient(
        financials={
            ("balance_sheet", "yearly"): annual,
            ("balance_sheet", "quarterly"): quarterly,
        }
    )


def test_melt_long_format_both_freqs():
    client = _client_with_balance()
    delta, new_keys = extract_financials(
        client, "AAPL", Dataset.BALANCE_SHEET, DatasetState(), LOAD_ID, currency="USD"
    )
    assert list(delta.columns) == FINANCIALS_COLUMNS
    assert set(delta["freq"]) == {"annual", "quarterly"}
    assert delta["statement"].eq("balance_sheet").all()
    # all-NaN value (TotalDebt 2025) dropped
    assert not delta["value"].isna().any()
    # sorted by ticker, freq, period_end, line_item
    assert delta.equals(
        delta.sort_values(["ticker", "freq", "period_end", "line_item"]).reset_index(drop=True)
    )
    assert "annual|2025-09-30" in new_keys
    assert "quarterly|2026-03-31" in new_keys


def test_incremental_drops_known_periods():
    client = _client_with_balance()
    state = DatasetState(period_ends=["annual|2024-09-30", "annual|2025-09-30"])
    delta, new_keys = extract_financials(
        client, "AAPL", Dataset.BALANCE_SHEET, state, LOAD_ID, currency="USD"
    )
    # only the unseen quarterly period survives
    assert set(delta["period_end"]) == {"2026-03-31"}
    assert new_keys == ["quarterly|2026-03-31"]


def test_force_repulls_all_periods():
    client = _client_with_balance()
    state = DatasetState(period_ends=["annual|2024-09-30", "annual|2025-09-30"])
    delta, _ = extract_financials(
        client, "AAPL", Dataset.BALANCE_SHEET, state, LOAD_ID, force=True, currency="USD"
    )
    assert "2024-09-30" in set(delta["period_end"])
    assert "2025-09-30" in set(delta["period_end"])


def test_empty_statements_noop():
    client = FakeYFClient(financials={})
    delta, new_keys = extract_financials(
        client, "AAPL", Dataset.CASH_FLOW, DatasetState(), LOAD_ID
    )
    assert delta.empty
    assert new_keys == []
