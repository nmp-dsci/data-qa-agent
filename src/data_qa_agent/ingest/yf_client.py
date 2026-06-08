"""Thin retry/backoff wrapper around yfinance.

yfinance/Yahoo throttles aggressively. Every network call routes through
``retry_call`` which adds exponential backoff with jitter. The wrapper deliberately
holds *no* yfinance objects at import time so tests can monkeypatch ``yfinance``.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Any, cast

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Exception types that signal a transient/throttled failure worth retrying.
# Kept as a plain tuple of names so we don't import yfinance-internal symbols.
_RETRYABLE_NAMES = {
    "YFRateLimitError",
    "YFDataException",
    "ConnectionError",
    "Timeout",
    "ReadTimeout",
    "ConnectTimeout",
    "HTTPError",
    "ChunkedEncodingError",
}


def _is_retryable(exc: BaseException) -> bool:
    # Walk the MRO names (some yfinance/requests errors subclass each other).
    mro_names = {cls.__name__ for cls in type(exc).__mro__}
    return bool(mro_names & _RETRYABLE_NAMES)


def retry_call[T](
    func: Callable[..., T],
    *args: Any,
    retries: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    **kwargs: Any,
) -> T:
    """Call ``func`` with exponential backoff + jitter on transient errors.

    Re-raises the last exception once ``retries`` is exhausted, or immediately for
    errors that are not classified as transient.
    """
    attempt = 0
    while True:
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - intentional broad retry gate
            attempt += 1
            if attempt > retries or not _is_retryable(exc):
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.25)
            logger.warning(
                "retry %d/%d after %s: %s", attempt, retries, type(exc).__name__, exc
            )
            time.sleep(delay)


class YFClient:
    """Retry-wrapped access to the subset of yfinance used by the extractor."""

    def __init__(self, retries: int = 4, base_delay: float = 1.0) -> None:
        self.retries = retries
        self.base_delay = base_delay

    def _ticker(self, ticker: str) -> Any:
        return yf.Ticker(ticker)

    def history(
        self,
        ticker: str,
        *,
        start: Any = None,
        end: Any = None,
        interval: str = "1d",
        auto_adjust: bool = False,
        actions: bool = True,
    ) -> pd.DataFrame:
        tk = self._ticker(ticker)
        return cast(
            pd.DataFrame,
            retry_call(
                tk.history,
                retries=self.retries,
                base_delay=self.base_delay,
                start=start,
                end=end,
                interval=interval,
                auto_adjust=auto_adjust,
                actions=actions,
            ),
        )

    def balance_sheet(self, ticker: str, *, freq: str) -> pd.DataFrame:
        tk = self._ticker(ticker)
        return cast(
            pd.DataFrame,
            retry_call(
                tk.get_balance_sheet, retries=self.retries, base_delay=self.base_delay, freq=freq
            ),
        )

    def income_stmt(self, ticker: str, *, freq: str) -> pd.DataFrame:
        tk = self._ticker(ticker)
        return cast(
            pd.DataFrame,
            retry_call(
                tk.get_income_stmt, retries=self.retries, base_delay=self.base_delay, freq=freq
            ),
        )

    def cash_flow(self, ticker: str, *, freq: str) -> pd.DataFrame:
        tk = self._ticker(ticker)
        return cast(
            pd.DataFrame,
            retry_call(
                tk.get_cash_flow, retries=self.retries, base_delay=self.base_delay, freq=freq
            ),
        )

    def info(self, ticker: str) -> dict[str, Any]:
        tk = self._ticker(ticker)
        data = retry_call(tk.get_info, retries=self.retries, base_delay=self.base_delay)
        return dict(data) if data else {}
