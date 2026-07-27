"""Turn counted tokens into a dollar figure (s32 W2).

The audit found tokens counted everywhere and priced nowhere, so "what does an
answer cost?" had no answer on any surface. This module is the missing half.

**The reason it has to be cache-aware.** This workload re-sends a large, stable
prefix every turn — the system prompt, the schema, the skills catalogue, the
recalled memories — so the overwhelming majority of ``input_tokens`` are
prompt-cache *hits*, which providers bill at roughly a tenth of a cache miss. A
flat price-per-input-token would overstate real spend by about 6x on this app's
traffic (measured, not assumed — see the token-cost note in the plan). A cost
tile that is wrong by 6x is worse than no cost tile, so the rate table splits
input three ways:

* **cache miss** — first sight of a prefix, full input price.
* **cache write** — the surcharge for *establishing* a cache entry (Anthropic
  bills this above the miss price; DeepSeek does not).
* **cache read** — a hit, an order of magnitude cheaper.

``input_tokens`` from pydantic-ai's usage object is the *total* input, with
``cache_read_tokens`` as the already-cheap subset, so the miss count is the
difference. Getting that subtraction backwards is exactly the 6x error, which is
why :func:`cost_usd` is pinned by a unit test against a known run.

Rates are USD per million tokens, set from each provider's published sheet at
the date named below. They live in one dict on purpose: when a provider changes
prices, the test fails and someone updates a constant — rather than the cost
tile silently mis-billing for months.
"""

from __future__ import annotations

from dataclasses import dataclass

# Rates verified against the providers' public pricing pages on 2026-07-26.
# USD per 1,000,000 tokens.
RATES_ASOF = "2026-07-26"


@dataclass(frozen=True)
class Rate:
    """Per-million-token prices for one model.

    ``cache_write`` is the price of an input token that also populates the cache
    and ``cache_read`` the price of one served from it. Where a provider does not
    charge a cache-write premium, ``cache_write == input_miss``.
    """

    input_miss: float
    cache_write: float
    cache_read: float
    output: float


# Keyed by the model id the agent actually sends. Prefix matching (below) means
# a dated snapshot id like "claude-sonnet-4-6-20260514" resolves to its family.
RATES: dict[str, Rate] = {
    # DeepSeek (the default provider): cache hits are 10x cheaper than misses
    # and there is no separate write premium.
    "deepseek-chat": Rate(input_miss=0.27, cache_write=0.27, cache_read=0.027, output=1.10),
    "deepseek-reasoner": Rate(input_miss=0.55, cache_write=0.55, cache_read=0.14, output=2.19),
    # Anthropic: a 5-minute cache write costs 1.25x the miss price; a read 0.1x.
    "claude-opus-4": Rate(input_miss=15.0, cache_write=18.75, cache_read=1.50, output=75.0),
    "claude-sonnet-4": Rate(input_miss=3.0, cache_write=3.75, cache_read=0.30, output=15.0),
    "claude-haiku-4": Rate(input_miss=1.0, cache_write=1.25, cache_read=0.10, output=5.0),
}

# When a model id matches nothing, price it as the house default rather than
# returning None — an unpriced answer is invisible on the deck, and "roughly
# right" beats "silently missing". The fallback is named so a reader can tell
# an estimate from a quoted rate.
FALLBACK_MODEL = "deepseek-chat"


def rate_for(model_id: str | None) -> tuple[str, Rate]:
    """Resolve a model id to (matched key, rate), longest prefix wins.

    Longest-prefix rather than exact match so dated snapshots
    ("claude-sonnet-4-6-20260514") and vendor prefixes price correctly without
    a table entry per release.
    """
    needle = (model_id or "").strip().lower()
    best: str | None = None
    for key in RATES:
        if needle.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    if best is None:
        return FALLBACK_MODEL, RATES[FALLBACK_MODEL]
    return best, RATES[best]


def cost_usd(
    *,
    model_id: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
) -> float | None:
    """Priced cost of one run, or None when there is nothing to price.

    ``input_tokens`` is the provider's TOTAL input count and
    ``cache_read_tokens``/``cache_write_tokens`` are subsets of it, so the
    full-price miss count is what remains after both are removed. Clamped at
    zero: a provider that reports a cache-read count exceeding its own input
    total must not produce a negative cost.
    """
    inp = max(0, int(input_tokens or 0))
    out = max(0, int(output_tokens or 0))
    cached = max(0, int(cache_read_tokens or 0))
    written = max(0, int(cache_write_tokens or 0))
    if inp == 0 and out == 0:
        return None

    _key, rate = rate_for(model_id)
    misses = max(0, inp - cached - written)
    total = (
        misses * rate.input_miss
        + written * rate.cache_write
        + cached * rate.cache_read
        + out * rate.output
    ) / 1_000_000
    # 6dp matches app.query_runs.cost_usd numeric(12,6) — a single answer costs
    # fractions of a cent, so anything coarser rounds most runs to zero.
    return round(total, 6)


def effective_input_tokens(input_tokens: int | None, cache_read_tokens: int | None) -> int | None:
    """Input tokens weighted by what they actually cost, as a miss-equivalent.

    Useful for the "nominal vs effective" comparison the cost panel makes: a run
    reporting 250k input tokens of which 210k were cache hits cost about what
    61k uncached tokens would. Uses a flat 10x cache discount, which is the
    ratio every provider in the table lands near — this is a display aid, not
    the billing path (that is :func:`cost_usd`).
    """
    inp = input_tokens or 0
    if inp <= 0:
        return None
    cached = min(max(0, cache_read_tokens or 0), inp)
    return int(round((inp - cached) + cached * 0.1))
