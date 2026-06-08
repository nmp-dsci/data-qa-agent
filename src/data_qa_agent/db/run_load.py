"""CLI for the LOAD stage (spec §2.5).

    uv run python -m data_qa_agent.db.run_load --datasets all
    uv run python -m data_qa_agent.db.run_load --datasets eod_prices,company_profile

Discovers raw CSVs and appends any not already recorded in ``_load_audit``.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .load import ALL_DATASETS, run_load

logger = logging.getLogger(__name__)


def _parse_datasets(value: str) -> list[str]:
    value = value.strip()
    if value == "" or value.lower() == "all":
        return list(ALL_DATASETS)
    names = [v.strip() for v in value.split(",") if v.strip()]
    unknown = [n for n in names if n not in ALL_DATASETS]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown dataset(s): {', '.join(unknown)}. "
            f"choose from: {', '.join(ALL_DATASETS)} (or 'all')"
        )
    return names


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="data_qa_agent.db.run_load",
        description="Append-only COPY load of raw CSVs into raw_yfinance.*",
    )
    parser.add_argument(
        "--datasets",
        type=_parse_datasets,
        default=list(ALL_DATASETS),
        help="comma-separated dataset names, or 'all' (default: all).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)

    result = run_load(datasets=args.datasets)
    counts = result.counts

    print(f"load_id: {result.load_id}")
    print(
        f"files: ok={counts['ok']} skipped={counts['skipped']} "
        f"failed={counts['failed']} | rows_inserted={result.rows_inserted}"
    )
    for r in result.results:
        line = f"  [{r.status}] {r.raw_file.source_file} -> {r.raw_file.dataset}"
        if r.status == "ok":
            line += f" ({r.rows_inserted} rows)"
        elif r.status == "failed":
            line += f" ({r.message})"
        print(line)

    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
