from __future__ import annotations

import pytest

from data_qa_agent.ingest.universe import (
    load_sp500_seed,
    normalize_ticker,
    resolve_tickers,
)


def test_normalize_class_share():
    assert normalize_ticker("brk.b") == "BRK-B"
    assert normalize_ticker(" aapl ") == "AAPL"
    assert normalize_ticker("BF.B") == "BF-B"


def test_resolve_explicit_wins_and_dedups():
    assert resolve_tickers(["AAPL", "msft", "AAPL"], "sp500") == ["AAPL", "MSFT"]


def test_resolve_requires_input():
    with pytest.raises(ValueError):
        resolve_tickers(None, None)


def test_resolve_unknown_universe():
    with pytest.raises(ValueError):
        resolve_tickers(None, "russell")


def test_load_seed_committed(tmp_path):
    seed = tmp_path / "seed.csv"
    seed.write_text(
        "ticker,company_name\nMSFT,Microsoft\nbrk.b,Berkshire\nAAPL,Apple\n"
    )
    assert load_sp500_seed(seed) == ["AAPL", "BRK-B", "MSFT"]


def test_resolve_sp500_reads_seed(tmp_path):
    seed = tmp_path / "seed.csv"
    seed.write_text("ticker\nAAPL\nMSFT\n")
    assert resolve_tickers(None, "sp500", seed_path=seed) == ["AAPL", "MSFT"]


def test_committed_seed_loads():
    # The real committed seed must parse and include the normalization sample.
    tickers = load_sp500_seed()
    assert "AAPL" in tickers
    assert "BRK-B" in tickers
