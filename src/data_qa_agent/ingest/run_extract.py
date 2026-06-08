"""CLI orchestration for the EXTRACT stage + run summary.

Usage::

    python -m data_qa_agent.ingest.run_extract --tickers AAPL --years 10
    python -m data_qa_agent.ingest.run_extract --universe sp500 --sleep 0.5
    python -m data_qa_agent.ingest.run_extract --tickers AAPL --datasets eod_prices
    python -m data_qa_agent.ingest.run_extract --tickers AAPL --force
    python -m data_qa_agent.ingest.run_extract --tickers AAPL --force-dataset eod_prices
    python -m data_qa_agent.ingest.run_extract --tickers AAPL --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

from data_qa_agent.config import DATA_RAW_DIR

from . import landing
from .extract_financials import extract_financials
from .extract_prices import extract_prices
from .extract_profile import extract_profile
from .models import (
    ALL_DATASETS,
    PERIOD_GRAINED,
    Dataset,
    DatasetState,
    RunSummary,
    TickerResult,
)
from .universe import resolve_tickers
from .yf_client import YFClient

logger = logging.getLogger(__name__)


def _parse_datasets(value: str | None) -> list[Dataset]:
    if not value:
        return list(ALL_DATASETS)
    out: list[Dataset] = []
    for raw in value.split(","):
        name = raw.strip()
        if not name:
            continue
        try:
            out.append(Dataset(name))
        except ValueError as exc:
            valid = ", ".join(d.value for d in ALL_DATASETS)
            raise ValueError(f"unknown dataset {name!r}; valid: {valid}") from exc
    return out


def _write(
    dataset: Dataset,
    ticker: str,
    load_id: str,
    df: pd.DataFrame,
    raw_dir: Path,
    dry_run: bool,
) -> tuple[str, int, str | None]:
    """Write a delta (or simulate it). Returns ``(status, rows, file)``."""
    if df is None or df.empty:
        return "noop", 0, None
    if dry_run:
        return "ok", len(df), str(landing.delta_path(dataset, ticker, load_id, raw_dir))
    path = landing.write_delta_file(dataset, ticker, load_id, df, raw_dir)
    return "ok", len(df), (str(path) if path else None)


def run_extract(
    tickers: list[str],
    datasets: list[Dataset],
    *,
    years: int = 10,
    force: bool = False,
    force_datasets: set[Dataset] | None = None,
    sleep: float = 0.0,
    dry_run: bool = False,
    raw_dir: Path | None = None,
    client: YFClient | None = None,
    today: date | None = None,
    load_id: str | None = None,
) -> RunSummary:
    """Run the extractor for the given tickers/datasets. Per-ticker failure isolation.

    ``load_id`` (the run's ``YYYYMMDDHHMM`` UTC pull timestamp) defaults to now; it is
    injectable for tests so a forced re-pull within the same minute gets a fresh name.
    """
    raw_dir = raw_dir or DATA_RAW_DIR
    client = client or YFClient()
    today = today or date.today()
    force_datasets = force_datasets or set()

    load_id = load_id or landing.now_load_id()
    summary = RunSummary(load_id=load_id, dry_run=dry_run)
    state = landing.load_state(raw_dir)
    selected = set(datasets)

    for i, ticker in enumerate(tickers):
        try:
            _run_one_ticker(
                client=client,
                ticker=ticker,
                selected=selected,
                state=state,
                load_id=load_id,
                summary=summary,
                years=years,
                force=force,
                force_datasets=force_datasets,
                dry_run=dry_run,
                raw_dir=raw_dir,
                today=today,
            )
        except Exception as exc:  # noqa: BLE001 - isolate per-ticker failure
            logger.exception("ticker %s failed", ticker)
            for ds in datasets:
                summary.add(
                    TickerResult(
                        ticker=ticker, dataset=ds, status="failed", reason=str(exc)
                    )
                )
        if sleep and i < len(tickers) - 1:
            time.sleep(sleep)

    if not dry_run:
        landing.save_state(state, raw_dir)
    return summary


def _ds_forced(ds: Dataset, force: bool, force_datasets: set[Dataset]) -> bool:
    return force or ds in force_datasets


def _run_one_ticker(
    *,
    client: YFClient,
    ticker: str,
    selected: set[Dataset],
    state: dict[str, dict[str, DatasetState]],
    load_id: str,
    summary: RunSummary,
    years: int,
    force: bool,
    force_datasets: set[Dataset],
    dry_run: bool,
    raw_dir: Path,
    today: date,
) -> None:
    # Profile / info first so we can reuse currency + info across datasets.
    info: dict[str, object] | None = None
    currency: str | None = None
    needs_info = bool(selected & {Dataset.COMPANY_PROFILE, Dataset.EOD_PRICES})
    if needs_info:
        try:
            info = client.info(ticker)
            cur = info.get("currency")
            currency = str(cur) if cur else None
        except Exception:  # noqa: BLE001 - info is best-effort metadata
            info = None

    # --- company_profile (snapshot, one per UTC day) ---
    if Dataset.COMPANY_PROFILE in selected:
        ds = Dataset.COMPANY_PROFILE
        st = landing.get_dataset_state(state, ticker, ds)
        run_day = landing.load_id_to_iso(load_id)[:10]
        if not _ds_forced(ds, force, force_datasets) and st.last_date == run_day:
            summary.add(TickerResult(ticker=ticker, dataset=ds, status="noop"))
        else:
            df, _ = extract_profile(client, ticker, load_id, info=info)
            status, rows, fpath = _write(ds, ticker, load_id, df, raw_dir, dry_run)
            summary.add(
                TickerResult(ticker=ticker, dataset=ds, status=status, rows=rows, file=fpath)
            )
            if status == "ok" and not dry_run:
                landing.set_dataset_state(
                    state, ticker, ds, DatasetState(last_date=run_day)
                )

    # --- prices + corporate actions (share one history pull) ---
    if selected & {Dataset.EOD_PRICES, Dataset.CORPORATE_ACTIONS}:
        eod_state = landing.get_dataset_state(state, ticker, Dataset.EOD_PRICES)
        result = extract_prices(
            client,
            ticker,
            eod_state,
            load_id,
            years=years,
            today=today,
            force=_ds_forced(Dataset.EOD_PRICES, force, force_datasets),
            currency=currency,
        )
        if Dataset.EOD_PRICES in selected:
            status, rows, fpath = _write(
                Dataset.EOD_PRICES, ticker, load_id, result.eod_prices, raw_dir, dry_run
            )
            summary.add(
                TickerResult(
                    ticker=ticker, dataset=Dataset.EOD_PRICES, status=status, rows=rows, file=fpath
                )
            )
            if status == "ok" and not dry_run and result.new_last_date:
                landing.set_dataset_state(
                    state, ticker, Dataset.EOD_PRICES, DatasetState(last_date=result.new_last_date)
                )
        if Dataset.CORPORATE_ACTIONS in selected:
            ca_state = landing.get_dataset_state(state, ticker, Dataset.CORPORATE_ACTIONS)
            status, rows, fpath = _write(
                Dataset.CORPORATE_ACTIONS,
                ticker,
                load_id,
                result.corporate_actions,
                raw_dir,
                dry_run,
            )
            summary.add(
                TickerResult(
                    ticker=ticker,
                    dataset=Dataset.CORPORATE_ACTIONS,
                    status=status,
                    rows=rows,
                    file=fpath,
                )
            )
            # Advance the corporate-actions watermark to the prices window end so we
            # never re-scan dividend/split history we've already swept (even days with
            # zero actions). Only advance forward.
            if not dry_run and not _ds_forced(
                Dataset.CORPORATE_ACTIONS, force, force_datasets
            ):
                swept = result.new_last_date
                if swept and (ca_state.last_date is None or swept > ca_state.last_date):
                    landing.set_dataset_state(
                        state, ticker, Dataset.CORPORATE_ACTIONS, DatasetState(last_date=swept)
                    )

    # --- financials (period-grained) ---
    for ds in (Dataset.BALANCE_SHEET, Dataset.INCOME_STATEMENT, Dataset.CASH_FLOW):
        if ds not in selected:
            continue
        st = landing.get_dataset_state(state, ticker, ds)
        delta, new_keys = extract_financials(
            client,
            ticker,
            ds,
            st,
            load_id,
            force=_ds_forced(ds, force, force_datasets),
            currency=currency,
        )
        status, rows, fpath = _write(ds, ticker, load_id, delta, raw_dir, dry_run)
        summary.add(
            TickerResult(ticker=ticker, dataset=ds, status=status, rows=rows, file=fpath)
        )
        if status == "ok" and not dry_run and ds in PERIOD_GRAINED:
            merged = sorted(set(st.period_ends) | set(new_keys))
            landing.set_dataset_state(state, ticker, ds, DatasetState(period_ends=merged))


def _print_summary(summary: RunSummary) -> None:
    counts = summary.counts
    print(
        f"\nRun {summary.load_id}"
        f"{' (dry-run)' if summary.dry_run else ''}: "
        f"ok={counts['ok']} noop={counts['noop']} "
        f"skipped={counts['skipped']} failed={counts['failed']}"
    )
    for r in summary.results:
        line = f"  [{r.status:7}] {r.ticker:8} {r.dataset.value:18}"
        if r.rows:
            line += f" rows={r.rows}"
        if r.file:
            line += f" file={Path(r.file).name}"
        if r.reason:
            line += f" reason={r.reason}"
        print(line)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m data_qa_agent.ingest.run_extract",
        description="EXTRACT stage: pull yfinance data into immutable raw CSV deltas.",
    )
    p.add_argument("--tickers", help="comma-separated explicit tickers, e.g. AAPL,MSFT")
    p.add_argument("--universe", help="ticker universe, e.g. 'sp500' (reads the seed)")
    p.add_argument("--years", type=int, default=10, help="years of EOD history (default 10)")
    p.add_argument("--datasets", help="comma-separated datasets (default: all)")
    p.add_argument("--force", action="store_true", help="re-pull full window for all datasets")
    p.add_argument(
        "--force-dataset",
        help="comma-separated datasets to force re-pull (e.g. eod_prices)",
    )
    p.add_argument("--sleep", type=float, default=0.0, help="seconds to sleep between tickers")
    p.add_argument("--dry-run", action="store_true", help="compute deltas but write nothing")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = build_parser().parse_args(argv)

    explicit = args.tickers.split(",") if args.tickers else None
    try:
        tickers = resolve_tickers(explicit, args.universe)
        datasets = _parse_datasets(args.datasets)
        force_datasets = set(_parse_datasets(args.force_dataset)) if args.force_dataset else set()
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    summary = run_extract(
        tickers,
        datasets,
        years=args.years,
        force=args.force,
        force_datasets=force_datasets,
        sleep=args.sleep,
        dry_run=args.dry_run,
    )
    _print_summary(summary)
    return 1 if summary.counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
