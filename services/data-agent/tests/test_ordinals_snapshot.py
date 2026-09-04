"""ordinals_snapshot_hash (M3a) — a stable fingerprint of the effective ordinals
state (code seed BAND_ORDERS merged with the app.dataset_ordinals DB override),
so a query_run can record it and a past run's ordinal inputs stay reproducible
even after a curator later edits app.dataset_ordinals (see the
0036_dataset_ordinals_log migration, which gives that table version history).

No live DB is used here: load_overrides() is the module's one DB-fetch layer,
and every test monkeypatches it directly to control the override cache.
"""

from __future__ import annotations

import asyncio

from agent import ordinals


def _reset_cache() -> None:
    ordinals._OVERRIDES = None
    ordinals._loaded_at = 0.0


def _fake_load_overrides(overrides: dict[tuple[str, str], list[str]]):
    async def _load(*, ttl: float = ordinals._TTL_SECONDS) -> None:
        # Force-refresh every call so the fake stays authoritative regardless
        # of the 5s TTL — mirrors what a fresh load_overrides() call would do.
        ordinals._OVERRIDES = dict(overrides)
        ordinals._loaded_at = 0.0

    return _load


def test_hash_is_stable_across_calls(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.setattr(ordinals, "load_overrides", _fake_load_overrides({}))

    first = asyncio.run(ordinals.ordinals_snapshot_hash())
    second = asyncio.run(ordinals.ordinals_snapshot_hash())

    assert first == second
    assert len(first) == 64  # sha256 hex digest
    int(first, 16)  # valid hex


def test_hash_changes_when_an_override_differs(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.setattr(ordinals, "load_overrides", _fake_load_overrides({}))
    baseline = asyncio.run(ordinals.ordinals_snapshot_hash())

    _reset_cache()
    monkeypatch.setattr(
        ordinals,
        "load_overrides",
        _fake_load_overrides(
            {
                ("nsw_sales", "area_band"): [
                    "unknown",
                    "<400",
                    "400-700",
                    "700-1000",
                    "1000-5000",
                    "5000+",
                ]
            }
        ),
    )
    overridden = asyncio.run(ordinals.ordinals_snapshot_hash())

    assert overridden != baseline

    # Re-asserting the same override again reproduces the same hash — it's a
    # function of state, not of when/how many times it's called.
    _reset_cache()
    monkeypatch.setattr(
        ordinals,
        "load_overrides",
        _fake_load_overrides(
            {
                ("nsw_sales", "area_band"): [
                    "unknown",
                    "<400",
                    "400-700",
                    "700-1000",
                    "1000-5000",
                    "5000+",
                ]
            }
        ),
    )
    overridden_again = asyncio.run(ordinals.ordinals_snapshot_hash())
    assert overridden_again == overridden


def test_hash_works_when_db_is_unavailable(monkeypatch) -> None:
    """load_overrides() degrades an unreachable DB to a no-op (empty override
    cache) rather than raising — see its own try/except. The hash must not
    raise either, and must fall back to hashing the code seed alone."""
    _reset_cache()

    async def _db_unavailable(*, ttl: float = ordinals._TTL_SECONDS) -> None:
        # Mirrors load_overrides' except-branch: only backfill _OVERRIDES if
        # it isn't already populated — a real DB failure never clears a good
        # cache, it just fails to refresh it.
        if ordinals._OVERRIDES is None:
            ordinals._OVERRIDES = {}

    monkeypatch.setattr(ordinals, "load_overrides", _db_unavailable)

    seed_only_hash = asyncio.run(ordinals.ordinals_snapshot_hash())
    assert len(seed_only_hash) == 64

    # Same result as an explicit empty-override state — proves the DB-down
    # path and the "DB reachable but has no overrides" path agree.
    _reset_cache()
    monkeypatch.setattr(ordinals, "load_overrides", _fake_load_overrides({}))
    explicit_empty_hash = asyncio.run(ordinals.ordinals_snapshot_hash())

    assert seed_only_hash == explicit_empty_hash
