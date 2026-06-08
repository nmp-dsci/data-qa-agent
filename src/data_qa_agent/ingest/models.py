"""Pydantic v2 models for the extractor: dataset names, watermark, run summary."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Dataset(StrEnum):
    """The raw datasets the extractor produces, one subdir each under ``data/raw``."""

    EOD_PRICES = "eod_prices"
    CORPORATE_ACTIONS = "corporate_actions"
    BALANCE_SHEET = "balance_sheet"
    INCOME_STATEMENT = "income_statement"
    CASH_FLOW = "cash_flow"
    COMPANY_PROFILE = "company_profile"


ALL_DATASETS: tuple[Dataset, ...] = tuple(Dataset)

# Datasets whose incremental cursor is a date watermark.
DATE_GRAINED: frozenset[Dataset] = frozenset(
    {Dataset.EOD_PRICES, Dataset.CORPORATE_ACTIONS}
)
# Datasets whose incremental cursor is a set of (freq, period_end).
PERIOD_GRAINED: frozenset[Dataset] = frozenset(
    {Dataset.BALANCE_SHEET, Dataset.INCOME_STATEMENT, Dataset.CASH_FLOW}
)


class DatasetState(BaseModel):
    """Watermark for one ticker x dataset.

    ``last_date`` (ISO ``YYYY-MM-DD``) is used by date-grained datasets.
    ``period_ends`` is the set of ``"freq|period_end"`` keys already fetched, used by
    period-grained datasets.
    """

    last_date: str | None = None
    period_ends: list[str] = Field(default_factory=list)


class TickerResult(BaseModel):
    """Per ticker x dataset outcome for the run summary."""

    ticker: str
    dataset: Dataset
    status: str  # ok | noop | skipped | failed
    rows: int = 0
    file: str | None = None
    reason: str | None = None


class RunSummary(BaseModel):
    """Aggregate outcome of one extract run."""

    load_id: str  # the run's UTC pull timestamp, YYYYMMDDHHMM
    dry_run: bool = False
    results: list[TickerResult] = Field(default_factory=list)

    def add(self, result: TickerResult) -> None:
        self.results.append(result)

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {"ok": 0, "noop": 0, "skipped": 0, "failed": 0}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out
