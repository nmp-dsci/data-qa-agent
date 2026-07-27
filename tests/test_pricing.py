"""Cache-aware cost, pinned against a known run (s32 W2).

The plan's constraint #3 in one sentence: on this workload most input tokens are
prompt-cache *hits*, billed at roughly a tenth of a miss, so pricing input at a
flat rate overstates spend by about 6x. A cost tile that is wrong by 6x is worse
than no cost tile, so the arithmetic gets a test rather than a comment.

The pin is deliberate. Rates live in one dict and drift with the providers'
price sheets; when they change, this test fails and someone updates a constant —
instead of the deck quietly mis-billing for months. If you are here because this
test failed after a rate edit, that is the mechanism working: check the new rate
against the provider's page, then update the expected number.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-agent"))

from agent.pricing import (  # noqa: E402
    RATES,
    cost_usd,
    effective_input_tokens,
    rate_for,
)

# A real run's shape from this app's traffic: a big prompt re-sent every turn, so
# input is dominated by cache reads. Numbers rounded from an observed heavy
# two-dataset question (~252k nominal input tokens).
KNOWN_RUN = {
    "model_id": "deepseek-chat",
    "input_tokens": 252_000,
    "output_tokens": 6_000,
    "cache_read_tokens": 220_000,
    "cache_write_tokens": 8_000,
}


def test_known_run_prices_to_the_pinned_figure() -> None:
    # misses = 252_000 - 220_000 - 8_000 = 24_000
    #   24_000 * 0.27  / 1e6 = 0.006480
    #    8_000 * 0.27  / 1e6 = 0.002160
    #  220_000 * 0.027 / 1e6 = 0.005940
    #    6_000 * 1.10  / 1e6 = 0.006600
    #                        = 0.021180
    assert cost_usd(**KNOWN_RUN) == 0.02118


def test_ignoring_the_cache_split_overstates_spend_several_fold() -> None:
    """The 6x error the plan called out, demonstrated rather than asserted."""
    honest = cost_usd(**KNOWN_RUN)
    naive = cost_usd(
        model_id=KNOWN_RUN["model_id"],
        input_tokens=KNOWN_RUN["input_tokens"],
        output_tokens=KNOWN_RUN["output_tokens"],
        # No split supplied → every input token priced as a miss.
    )
    assert honest is not None and naive is not None
    # The input half is ~10x off; blended with output it lands around 3-4x here.
    assert naive > honest * 3


def test_nothing_to_price_returns_none() -> None:
    # None, not 0.0: "this run had no model spend" and "this run cost nothing"
    # read the same on a dashboard, and only one of them is informative. The
    # rollup counts priced rows separately for exactly this reason.
    assert cost_usd(model_id="deepseek-chat", input_tokens=None, output_tokens=None) is None
    assert cost_usd(model_id="deepseek-chat", input_tokens=0, output_tokens=0) is None


def test_cache_read_larger_than_input_cannot_go_negative() -> None:
    # Defensive: providers have shipped inconsistent usage payloads before, and a
    # negative cost silently subtracts from the deck's total.
    cost = cost_usd(
        model_id="deepseek-chat",
        input_tokens=1_000,
        output_tokens=0,
        cache_read_tokens=5_000,
    )
    assert cost is not None and cost >= 0


def test_dated_snapshot_ids_resolve_to_their_family() -> None:
    # Model ids carry dates in production; pricing must not fall back to the
    # house default just because a snapshot suffix appeared.
    key, rate = rate_for("claude-sonnet-4-6-20260514")
    assert key == "claude-sonnet-4"
    assert rate is RATES["claude-sonnet-4"]


def test_unknown_model_falls_back_rather_than_returning_nothing() -> None:
    key, _rate = rate_for("some-new-model-v9")
    assert key in RATES
    assert cost_usd(model_id="some-new-model-v9", input_tokens=1000, output_tokens=100) is not None


def test_longest_prefix_wins() -> None:
    # "deepseek-reasoner" must not be priced as "deepseek-chat" (or vice versa)
    # because one prefix happens to be checked first.
    assert rate_for("deepseek-reasoner")[0] == "deepseek-reasoner"
    assert rate_for("deepseek-chat")[0] == "deepseek-chat"


def test_effective_input_tokens_is_a_miss_equivalent() -> None:
    # 252k nominal with 220k cached ≈ 54k miss-equivalent, i.e. the "~1/6th of
    # nominal" figure the plan quotes.
    effective = effective_input_tokens(252_000, 220_000)
    assert effective is not None
    assert 45_000 < effective < 60_000
    assert effective_input_tokens(0, 0) is None


def test_every_rate_orders_read_below_miss_below_output() -> None:
    """A transposed column in the table would be invisible without this."""
    for name, rate in RATES.items():
        assert rate.cache_read < rate.input_miss, name
        assert rate.cache_write >= rate.input_miss, name
        assert rate.output > rate.input_miss, name
