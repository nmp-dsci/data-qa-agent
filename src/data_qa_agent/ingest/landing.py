"""Landing zone: immutable delta CSV writes + the ``_state.json`` watermark.

Raw files are immutable, append-only per-pull deltas. This module owns:

- ``write_delta_file`` — writes one ``<dataset>/<ticker>_<ts>.csv`` (never overwrites).
- The watermark manifest at ``data/raw/_state.json`` (mutable metadata).
- ``rebuild_state`` — reconstructs the watermark by scanning existing raw files when
  the manifest is missing or corrupt (the raw files remain the source of truth).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from data_qa_agent.config import DATA_RAW_DIR

from .models import (
    ALL_DATASETS,
    DATE_GRAINED,
    PERIOD_GRAINED,
    Dataset,
    DatasetState,
)

logger = logging.getLogger(__name__)

STATE_FILENAME = "_state.json"
# <TICKER>_<YYYYMMDDHHMM>.csv  (ticker may contain a hyphen, e.g. BRK-B)
_FILENAME_RE = re.compile(r"^(?P<ticker>[A-Z0-9\-]+)_(?P<ts>\d{12})\.csv$")


def now_load_id() -> str:
    """The run's UTC pull timestamp (``YYYYMMDDHHMM``), used as filename + ``ingested_at``."""
    return datetime.now(UTC).strftime("%Y%m%d%H%M")


def load_id_to_iso(load_id: str) -> str:
    """Render a ``YYYYMMDDHHMM`` load_id as an ISO-8601 UTC timestamp for ``ingested_at``."""
    dt = datetime.strptime(load_id, "%Y%m%d%H%M").replace(tzinfo=UTC)
    return dt.isoformat()


def dataset_dir(dataset: Dataset, raw_dir: Path | None = None) -> Path:
    return (raw_dir or DATA_RAW_DIR) / dataset.value


def delta_path(dataset: Dataset, ticker: str, load_id: str, raw_dir: Path | None = None) -> Path:
    return dataset_dir(dataset, raw_dir) / f"{ticker}_{load_id}.csv"


def write_delta_file(
    dataset: Dataset,
    ticker: str,
    load_id: str,
    df: pd.DataFrame,
    raw_dir: Path | None = None,
) -> Path | None:
    """Write a delta CSV. Returns the path, or ``None`` if there were no rows.

    Refuses to overwrite an existing file (raw is immutable).
    """
    if df is None or df.empty:
        return None
    path = delta_path(dataset, ticker, load_id, raw_dir)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite immutable raw file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    logger.info("wrote %d rows -> %s", len(df), path)
    return path


# --------------------------------------------------------------------------- #
# Watermark manifest
# --------------------------------------------------------------------------- #


def _state_path(raw_dir: Path | None = None) -> Path:
    return (raw_dir or DATA_RAW_DIR) / STATE_FILENAME


def load_state(raw_dir: Path | None = None) -> dict[str, dict[str, DatasetState]]:
    """Load the watermark manifest, rebuilding from raw files if missing/corrupt.

    Shape: ``{ticker: {dataset_value: DatasetState}}``.
    """
    path = _state_path(raw_dir)
    if not path.exists():
        logger.info("no %s; rebuilding watermark from raw files", STATE_FILENAME)
        return rebuild_state(raw_dir)
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("corrupt %s (%s); rebuilding from raw files", STATE_FILENAME, exc)
        return rebuild_state(raw_dir)
    state: dict[str, dict[str, DatasetState]] = {}
    for ticker, datasets in raw.items():
        state[ticker] = {ds: DatasetState(**val) for ds, val in datasets.items()}
    return state


def save_state(state: dict[str, dict[str, DatasetState]], raw_dir: Path | None = None) -> None:
    path = _state_path(raw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {
        ticker: {ds: st.model_dump() for ds, st in datasets.items()}
        for ticker, datasets in state.items()
    }
    path.write_text(json.dumps(serializable, indent=2, sort_keys=True))


def get_dataset_state(
    state: dict[str, dict[str, DatasetState]], ticker: str, dataset: Dataset
) -> DatasetState:
    return state.get(ticker, {}).get(dataset.value, DatasetState())


def set_dataset_state(
    state: dict[str, dict[str, DatasetState]],
    ticker: str,
    dataset: Dataset,
    new: DatasetState,
) -> None:
    state.setdefault(ticker, {})[dataset.value] = new


def period_key(freq: str, period_end: str) -> str:
    return f"{freq}|{period_end}"


def rebuild_state(raw_dir: Path | None = None) -> dict[str, dict[str, DatasetState]]:
    """Reconstruct the watermark by scanning raw filenames + their contents."""
    base = raw_dir or DATA_RAW_DIR
    state: dict[str, dict[str, DatasetState]] = {}
    for dataset in ALL_DATASETS:
        ddir = base / dataset.value
        if not ddir.is_dir():
            continue
        for fpath in sorted(ddir.glob("*.csv")):
            m = _FILENAME_RE.match(fpath.name)
            if not m:
                continue
            ticker = m.group("ticker")
            try:
                df = pd.read_csv(fpath)
            except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
                continue
            cur = state.setdefault(ticker, {}).setdefault(dataset.value, DatasetState())
            if dataset in DATE_GRAINED and "date" in df.columns and not df.empty:
                fmax = str(pd.to_datetime(df["date"]).max().date())
                if cur.last_date is None or fmax > cur.last_date:
                    cur.last_date = fmax
            elif dataset in PERIOD_GRAINED and {"freq", "period_end"} <= set(df.columns):
                keys = {
                    period_key(str(f), str(pd.to_datetime(p).date()))
                    for f, p in zip(df["freq"], df["period_end"], strict=False)
                }
                merged = set(cur.period_ends) | keys
                cur.period_ends = sorted(merged)
    return state
