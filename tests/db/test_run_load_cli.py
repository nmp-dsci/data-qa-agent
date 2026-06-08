"""Unit tests for the run_load CLI argument parsing (no DB needed)."""

from __future__ import annotations

import argparse

import pytest

from data_qa_agent.db.load import ALL_DATASETS
from data_qa_agent.db.run_load import _parse_datasets


def test_parse_all_keyword() -> None:
    assert _parse_datasets("all") == list(ALL_DATASETS)


def test_parse_empty_defaults_to_all() -> None:
    assert _parse_datasets("") == list(ALL_DATASETS)


def test_parse_specific_list() -> None:
    assert _parse_datasets("eod_prices, company_profile") == ["eod_prices", "company_profile"]


def test_parse_rejects_unknown() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="unknown dataset"):
        _parse_datasets("eod_prices,bogus")
