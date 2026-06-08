"""Unit tests for file discovery + the dataset->table mapping (no DB needed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from data_qa_agent.db.load import (
    ALL_DATASETS,
    DATASET_TARGETS,
    RawFile,
    discover_files,
)


def test_all_datasets_have_targets() -> None:
    assert set(ALL_DATASETS) == set(DATASET_TARGETS)


def test_three_financial_dirs_map_to_one_table() -> None:
    tables = {DATASET_TARGETS[d].table for d in ("balance_sheet", "income_statement", "cash_flow")}
    assert tables == {"financial_statements"}


def test_source_file_is_dataset_qualified_and_unique() -> None:
    # Same filename in two dataset dirs must yield distinct audit keys.
    bs = RawFile("balance_sheet", "AAPL", Path("/x/balance_sheet/AAPL_202606040959.csv"))
    cf = RawFile("cash_flow", "AAPL", Path("/x/cash_flow/AAPL_202606040959.csv"))
    assert bs.source_file == "balance_sheet/AAPL_202606040959.csv"
    assert cf.source_file == "cash_flow/AAPL_202606040959.csv"
    assert bs.source_file != cf.source_file


def test_discover_finds_all_six(sample_raw_dir: Path) -> None:
    files = discover_files(raw_dir=sample_raw_dir)
    assert len(files) == 6
    assert {f.dataset for f in files} == set(ALL_DATASETS)


def test_discover_filters_by_dataset(sample_raw_dir: Path) -> None:
    files = discover_files(["eod_prices"], raw_dir=sample_raw_dir)
    assert [f.dataset for f in files] == ["eod_prices"]


def test_discover_sorted_chronologically(tmp_path: Path) -> None:
    ddir = tmp_path / "company_profile"
    ddir.mkdir(parents=True)
    header = "ticker,company_name,sector,industry,currency,exchange,country,ingested_at\n"
    for ts in ("202606041000", "202606040959", "202606040800"):
        (ddir / f"AAPL_{ts}.csv").write_text(header, encoding="utf-8")
    files = discover_files(["company_profile"], raw_dir=tmp_path)
    names = [f.path.name for f in files]
    assert names == sorted(names)  # oldest-first by embedded timestamp


def test_discover_ignores_nonconforming_names(tmp_path: Path) -> None:
    ddir = tmp_path / "eod_prices"
    ddir.mkdir(parents=True)
    (ddir / "not_a_match.csv").write_text("x\n", encoding="utf-8")
    (ddir / "README.txt").write_text("x\n", encoding="utf-8")
    assert discover_files(["eod_prices"], raw_dir=tmp_path) == []


def test_discover_rejects_unknown_dataset(sample_raw_dir: Path) -> None:
    with pytest.raises(ValueError, match="unknown dataset"):
        discover_files(["nope"], raw_dir=sample_raw_dir)
