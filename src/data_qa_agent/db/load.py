"""Append-only COPY load of raw CSVs into ``raw_yfinance.*`` (spec §2.3).

This is a *pure* append: discover raw files, skip any already recorded as a
successful load in ``raw_yfinance._load_audit`` (file-level idempotency), and
``COPY`` the rest oldest-first. No staging table, no ``ON CONFLICT``, no
``UPDATE``/``DELETE``, no dedup. Duplicate / restated rows land verbatim; the dbt
TRANSFORM stage (§3) dedups downstream.

Each file is loaded in its OWN transaction so one bad file does not poison the
batch. One ``load_id`` (uuid) is generated per run and stamped onto every row.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import psycopg

from data_qa_agent.config import DATA_RAW_DIR

from .connection import connect

logger = logging.getLogger(__name__)

# <TICKER>_<YYYYMMDDHHMM>.csv  (ticker may contain a hyphen, e.g. BRK-B)
_FILENAME_RE = re.compile(r"^(?P<ticker>[A-Z0-9\-]+)_(?P<ts>\d{12})\.csv$")


@dataclass(frozen=True)
class TargetTable:
    """Maps a raw dataset directory to its destination table + ordered data columns.

    ``data_columns`` is the explicit, CSV-header-order list of columns to COPY.
    The loader appends ``load_id`` and ``source_file`` after these; ``_row_id``
    and ``_loaded_at`` default automatically.
    """

    table: str
    data_columns: tuple[str, ...]


# CSV header orders mirror §1.3 / the real AAPL files exactly.
_EOD_COLS = (
    "ticker", "date", "open", "high", "low", "close",
    "adj_close", "volume", "currency", "source", "ingested_at",
)
_CORP_COLS = ("ticker", "date", "action_type", "value", "source", "ingested_at")
_FIN_COLS = (
    "ticker", "statement", "freq", "period_end", "line_item",
    "value", "currency", "source", "ingested_at",
)
_PROFILE_COLS = (
    "ticker", "company_name", "sector", "industry",
    "currency", "exchange", "country", "ingested_at",
)

# dataset dir name -> target table. The three financial statement dirs all map to
# the single financial_statements table (§2.2).
DATASET_TARGETS: dict[str, TargetTable] = {
    "eod_prices": TargetTable("eod_prices", _EOD_COLS),
    "corporate_actions": TargetTable("corporate_actions", _CORP_COLS),
    "balance_sheet": TargetTable("financial_statements", _FIN_COLS),
    "income_statement": TargetTable("financial_statements", _FIN_COLS),
    "cash_flow": TargetTable("financial_statements", _FIN_COLS),
    "company_profile": TargetTable("company_profile", _PROFILE_COLS),
}

ALL_DATASETS: tuple[str, ...] = tuple(DATASET_TARGETS)


@dataclass(frozen=True)
class RawFile:
    """A discovered raw CSV awaiting load."""

    dataset: str
    ticker: str
    path: Path

    @property
    def source_file(self) -> str:
        """The audit / lineage key, unique across the landing zone.

        Raw filenames are NOT unique on their own: every dataset run writes the
        same ``<TICKER>_<ts>.csv`` name into its own dataset dir, so all six of a
        run's files share a filename. We therefore key on the dataset-qualified
        path ``<dataset>/<filename>``, which is globally unique and still
        satisfies the spec's ``UNIQUE(source_file)`` ledger (§2.2).
        """
        return f"{self.dataset}/{self.path.name}"


@dataclass
class FileLoadResult:
    """Outcome of loading (or skipping) one raw file."""

    raw_file: RawFile
    status: str  # ok | skipped | failed
    rows_inserted: int = 0
    message: str | None = None


@dataclass
class LoadRunResult:
    """Aggregate outcome of one load run."""

    load_id: UUID
    results: list[FileLoadResult] = field(default_factory=list)

    @property
    def rows_inserted(self) -> int:
        return sum(r.rows_inserted for r in self.results)

    @property
    def counts(self) -> dict[str, int]:
        out = {"ok": 0, "skipped": 0, "failed": 0}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def discover_files(
    datasets: Iterable[str] | None = None,
    raw_dir: Path | None = None,
) -> list[RawFile]:
    """Find raw CSVs for the requested datasets, sorted oldest-first.

    Filenames embed a ``YYYYMMDDHHMM`` timestamp so a lexical sort by name is
    chronological (§1.3). Returns one :class:`RawFile` per matching CSV.
    """
    base = raw_dir or DATA_RAW_DIR
    wanted = list(datasets) if datasets is not None else list(ALL_DATASETS)
    files: list[RawFile] = []
    for dataset in wanted:
        if dataset not in DATASET_TARGETS:
            raise ValueError(f"unknown dataset: {dataset!r}")
        ddir = base / dataset
        if not ddir.is_dir():
            continue
        for fpath in sorted(ddir.glob("*.csv")):
            m = _FILENAME_RE.match(fpath.name)
            if not m:
                logger.warning("skipping non-conforming filename: %s", fpath.name)
                continue
            files.append(RawFile(dataset=dataset, ticker=m.group("ticker"), path=fpath))
    # Global oldest-first order across datasets by the embedded timestamp, then name.
    files.sort(key=lambda rf: (rf.path.name, rf.dataset))
    return files


# --------------------------------------------------------------------------- #
# Audit ledger
# --------------------------------------------------------------------------- #


def already_loaded(conn: psycopg.Connection, source_file: str) -> bool:
    """True if ``source_file`` already has a successful (status='ok') audit row."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM raw_yfinance._load_audit "
            "WHERE source_file = %s AND status = 'ok' LIMIT 1",
            (source_file,),
        )
        return cur.fetchone() is not None


def _record_audit(
    conn: psycopg.Connection,
    *,
    load_id: UUID,
    raw_file: RawFile,
    rows_inserted: int,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    message: str | None,
) -> None:
    """Insert one ``_load_audit`` row. UNIQUE(source_file) enforces load-once."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO raw_yfinance._load_audit "
            "(load_id, dataset, ticker, source_file, rows_inserted, "
            " started_at, finished_at, status, message) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                str(load_id),
                raw_file.dataset,
                raw_file.ticker,
                raw_file.source_file,
                rows_inserted,
                started_at,
                finished_at,
                status,
                message,
            ),
        )


# --------------------------------------------------------------------------- #
# COPY append
# --------------------------------------------------------------------------- #


def copy_append(
    conn: psycopg.Connection,
    raw_file: RawFile,
    load_id: UUID,
) -> int:
    """COPY one raw file into its target table, stamping load lineage.

    Uses ``COPY raw_yfinance.<table> (<data cols>, load_id, source_file)
    FROM STDIN WITH (FORMAT csv, HEADER true)`` via psycopg 3's ``cursor.copy()``.
    The CSV's own columns stream verbatim; ``load_id`` / ``source_file`` are
    appended as constant trailing columns on every row. Returns rows COPYed.

    Does NOT commit — the caller owns the transaction (§2.3).
    """
    target = DATASET_TARGETS[raw_file.dataset]
    columns = (*target.data_columns, "load_id", "source_file")
    col_list = ", ".join(columns)
    copy_sql = (
        f"COPY raw_yfinance.{target.table} ({col_list}) "
        f"FROM STDIN WITH (FORMAT csv, HEADER true)"
    )
    # Append load_id + source_file as two extra CSV columns on each data row.
    # The COPY HEADER option discards the first line, so we prepend a matching
    # header line and let Postgres skip it.
    suffix = f",{load_id},{raw_file.source_file}"
    header_suffix = ",load_id,source_file"

    data_rows = 0
    with conn.cursor() as cur, cur.copy(copy_sql) as copy:
        with raw_file.path.open("r", encoding="utf-8", newline="") as fh:
            first = True
            for line in fh:
                stripped = line.rstrip("\n").rstrip("\r")
                if stripped == "":
                    continue  # ignore stray blank lines (e.g. trailing newline)
                if first:
                    copy.write(stripped + header_suffix + "\n")
                    first = False
                else:
                    copy.write(stripped + suffix + "\n")
                    data_rows += 1
    return data_rows


def load_file(
    conn: psycopg.Connection,
    raw_file: RawFile,
    load_id: UUID,
) -> FileLoadResult:
    """Load one file in its own transaction; record the audit row.

    Skips (status='skipped', no audit insert) if the file already loaded ok.
    On COPY failure rolls back the data and records a 'failed' audit row in a
    separate transaction so the batch can continue.
    """
    if already_loaded(conn, raw_file.source_file):
        logger.info("skip (already loaded): %s", raw_file.source_file)
        return FileLoadResult(raw_file=raw_file, status="skipped", rows_inserted=0)

    started_at = datetime.now(UTC)
    try:
        rows = copy_append(conn, raw_file, load_id)
        finished_at = datetime.now(UTC)
        _record_audit(
            conn,
            load_id=load_id,
            raw_file=raw_file,
            rows_inserted=rows,
            started_at=started_at,
            finished_at=finished_at,
            status="ok",
            message=None,
        )
        conn.commit()
        logger.info("loaded %d rows from %s", rows, raw_file.source_file)
        return FileLoadResult(raw_file=raw_file, status="ok", rows_inserted=rows)
    except Exception as exc:  # noqa: BLE001 - isolate per-file failure
        conn.rollback()
        finished_at = datetime.now(UTC)
        message = f"{type(exc).__name__}: {exc}"
        logger.error("failed to load %s: %s", raw_file.source_file, message)
        try:
            _record_audit(
                conn,
                load_id=load_id,
                raw_file=raw_file,
                rows_inserted=0,
                started_at=started_at,
                finished_at=finished_at,
                status="failed",
                message=message,
            )
            conn.commit()
        except Exception:  # noqa: BLE001 - audit best-effort
            conn.rollback()
        return FileLoadResult(
            raw_file=raw_file, status="failed", rows_inserted=0, message=message
        )


def run_load(
    datasets: Sequence[str] | None = None,
    raw_dir: Path | None = None,
    conn: psycopg.Connection | None = None,
) -> LoadRunResult:
    """Discover + append all (unloaded) raw files for ``datasets``.

    One ``load_id`` per run. Each file loads in its own transaction.
    """
    load_id = uuid4()
    files = discover_files(datasets, raw_dir=raw_dir)
    result = LoadRunResult(load_id=load_id)
    logger.info("load run %s: %d candidate file(s)", load_id, len(files))

    own_conn = conn is None
    db = conn if conn is not None else connect()
    try:
        for raw_file in files:
            result.results.append(load_file(db, raw_file, load_id))
    finally:
        if own_conn:
            db.close()
    return result
