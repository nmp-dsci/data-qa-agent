from __future__ import annotations

import pytest

from data_qa_agent.ingest import yf_client


class YFRateLimitError(Exception):
    """Mimics yfinance's retryable error by name (matched via MRO name set)."""


def test_retry_call_succeeds_after_transient(monkeypatch):
    monkeypatch.setattr(yf_client.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise YFRateLimitError("throttled")
        return "ok"

    assert yf_client.retry_call(flaky, retries=4, base_delay=0.0) == "ok"
    assert calls["n"] == 3


def test_retry_call_reraises_non_retryable(monkeypatch):
    monkeypatch.setattr(yf_client.time, "sleep", lambda *_: None)

    def boom():
        raise ValueError("permanent")

    with pytest.raises(ValueError):
        yf_client.retry_call(boom, retries=4, base_delay=0.0)


def test_retry_call_exhausts(monkeypatch):
    monkeypatch.setattr(yf_client.time, "sleep", lambda *_: None)

    def always():
        raise YFRateLimitError("nope")

    with pytest.raises(YFRateLimitError):
        yf_client.retry_call(always, retries=2, base_delay=0.0)
