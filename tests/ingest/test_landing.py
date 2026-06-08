from __future__ import annotations

import json

import pandas as pd
import pytest

from data_qa_agent.ingest import landing
from data_qa_agent.ingest.models import Dataset, DatasetState


def test_write_delta_and_no_overwrite(raw_dir):
    df = pd.DataFrame({"ticker": ["AAPL"], "date": ["2026-01-01"]})
    path = landing.write_delta_file(Dataset.EOD_PRICES, "AAPL", "202606041415", df, raw_dir)
    assert path is not None and path.exists()
    assert path.name == "AAPL_202606041415.csv"
    with pytest.raises(FileExistsError):
        landing.write_delta_file(Dataset.EOD_PRICES, "AAPL", "202606041415", df, raw_dir)


def test_write_empty_returns_none(raw_dir):
    assert (
        landing.write_delta_file(
            Dataset.EOD_PRICES, "AAPL", "202606041415", pd.DataFrame(), raw_dir
        )
        is None
    )


def test_state_roundtrip(raw_dir):
    state = {
        "AAPL": {
            "eod_prices": DatasetState(last_date="2026-01-05"),
            "balance_sheet": DatasetState(period_ends=["annual|2025-09-30"]),
        }
    }
    landing.save_state(state, raw_dir)
    loaded = landing.load_state(raw_dir)
    assert loaded["AAPL"]["eod_prices"].last_date == "2026-01-05"
    assert loaded["AAPL"]["balance_sheet"].period_ends == ["annual|2025-09-30"]


def test_load_id_to_iso():
    assert landing.load_id_to_iso("202606041415") == "2026-06-04T14:15:00+00:00"


def test_rebuild_state_from_raw_files(raw_dir):
    eod = pd.DataFrame(
        {"ticker": ["AAPL", "AAPL"], "date": ["2026-01-04", "2026-01-05"]}
    )
    landing.write_delta_file(Dataset.EOD_PRICES, "AAPL", "202601051200", eod, raw_dir)
    fin = pd.DataFrame(
        {
            "ticker": ["AAPL"],
            "freq": ["annual"],
            "period_end": ["2025-09-30"],
        }
    )
    landing.write_delta_file(Dataset.BALANCE_SHEET, "AAPL", "202601051200", fin, raw_dir)

    rebuilt = landing.rebuild_state(raw_dir)
    assert rebuilt["AAPL"]["eod_prices"].last_date == "2026-01-05"
    assert rebuilt["AAPL"]["balance_sheet"].period_ends == ["annual|2025-09-30"]


def test_corrupt_state_triggers_rebuild(raw_dir):
    eod = pd.DataFrame({"ticker": ["AAPL"], "date": ["2026-01-05"]})
    landing.write_delta_file(Dataset.EOD_PRICES, "AAPL", "202601051200", eod, raw_dir)
    (raw_dir / landing.STATE_FILENAME).write_text("{ this is not json")
    loaded = landing.load_state(raw_dir)
    assert loaded["AAPL"]["eod_prices"].last_date == "2026-01-05"


def test_missing_state_rebuilds(raw_dir):
    # No _state.json, no files -> empty state, not an error.
    assert landing.load_state(raw_dir) == {}


def test_state_file_is_valid_json(raw_dir):
    landing.save_state({"AAPL": {"eod_prices": DatasetState(last_date="2026-01-05")}}, raw_dir)
    data = json.loads((raw_dir / landing.STATE_FILENAME).read_text())
    assert data["AAPL"]["eod_prices"]["last_date"] == "2026-01-05"
