"""scripts/mlflow_registry.py — cmd_promote's resilience to a failing delete_alias.

DB-free: `_db()` is monkeypatched to a stub that records the SQL it would have
run instead of shelling out to `docker compose exec`, and `mlflow_client`'s
network calls are monkeypatched too — mirroring test_optimiser_common.py's
"stub the boundary, run the real logic" style.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import mlflow_client as mc  # noqa: E402
import mlflow_registry as reg  # noqa: E402
from eval_run import _lit  # noqa: E402


@pytest.fixture
def _stub_promote_inputs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Champion v3 / challenger v4; challenger wins 5 of 5 discordant pairs (p = 0.03125)."""
    monkeypatch.setattr(
        mc, "get_alias_version", lambda name, alias: {"champion": "3", "challenger": "4"}[alias]
    )
    monkeypatch.setattr(reg, "_version_info", lambda version: {"agent_version_id": f"av-{version}"})
    monkeypatch.setattr(
        reg,
        "_latest_eval",
        lambda avid: {"id": f"run-{avid}", "pack_version": "p1", "totals": {"pass_rate": 1.0}},
    )
    cases = {f"k{i}" for i in range(5)}
    monkeypatch.setattr(reg, "_graded_cases", lambda run_id: set(cases))
    monkeypatch.setattr(
        reg, "_passed_cases", lambda run_id: set(cases) if run_id == "run-av-4" else set()
    )
    monkeypatch.setattr(mc, "set_alias", lambda *a, **k: None)

    inserted: list[str] = []
    monkeypatch.setattr(reg, "_db", lambda: (_lit, inserted.append, lambda sql: ""))
    return inserted


def test_cmd_promote_records_the_promotion_even_when_delete_alias_fails(
    _stub_promote_inputs: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _boom(name: str, alias: str) -> None:
        raise mc.MlflowError("mlflow rejected the request")

    monkeypatch.setattr(mc, "delete_alias", _boom)

    reg.cmd_promote(argparse.Namespace(alpha=0.05))

    inserted = _stub_promote_inputs
    assert len(inserted) == 1, "the app.promotions row must still be written"
    assert "INSERT INTO app.promotions" in inserted[0]
    assert "'3'" in inserted[0] and "'4'" in inserted[0]

    err = capsys.readouterr().err
    assert "could not clear @challenger" in err
    assert "v4" in err


def test_cmd_promote_records_the_promotion_when_delete_alias_succeeds(
    _stub_promote_inputs: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(mc, "delete_alias", lambda name, alias: None)

    reg.cmd_promote(argparse.Namespace(alpha=0.05))

    assert len(_stub_promote_inputs) == 1
    assert "could not clear" not in capsys.readouterr().err


# ---- the promotion rule itself (pure) --------------------------------------


def test_mcnemar_one_sided_p_is_the_binomial_upper_tail() -> None:
    assert reg.mcnemar_one_sided_p(0, 0) == 1.0
    assert reg.mcnemar_one_sided_p(5, 0) == pytest.approx(0.5**5)
    assert reg.mcnemar_one_sided_p(4, 1) == pytest.approx(6 / 32)
    assert reg.mcnemar_one_sided_p(0, 3) == 1.0


def test_min_discordant_for_alpha() -> None:
    assert reg.min_discordant_for(0.05) == 5
    assert reg.min_discordant_for(0.01) == 7


def test_verdict_holds_on_a_tie_even_when_both_pass_everything() -> None:
    graded = {"a", "b"}
    v = reg.promotion_verdict(graded, graded, graded)
    assert v["promoted"] is False and v["discordant"] == 0 and v["p_value"] == 1.0
    assert v["min_discordant_for_alpha"] == 5


def test_verdict_holds_on_a_small_win_and_promotes_on_a_significant_one() -> None:
    graded = {f"k{i}" for i in range(8)}
    champ = {"k0", "k1"}
    small = reg.promotion_verdict(champ, champ | {"k2", "k3"}, graded)
    assert small["promoted"] is False and small["p_value"] == pytest.approx(0.25)
    big = reg.promotion_verdict(champ, graded, graded)
    assert big["promoted"] is True and big["discordant"] == 6 and big["flips"] == []


def test_verdict_counts_flips_against_the_challenger() -> None:
    graded = {f"k{i}" for i in range(7)}
    champ = {"k0"}
    chall = graded - {"k0"}  # 6 wins, 1 flip -> p = P(X>=6 | 7) = 8/128
    v = reg.promotion_verdict(champ, chall, graded)
    assert v["challenger_wins"] == sorted(chall) and v["flips"] == ["k0"]
    assert v["p_value"] == pytest.approx(8 / 128) and v["promoted"] is False


def test_verdict_only_pairs_cases_graded_by_both_runs() -> None:
    v = reg.promotion_verdict({"a"}, {"a", "zzz"}, {"a", "b"})
    assert v["cases_paired"] == 2 and v["challenger_wins"] == [] and v["discordant"] == 0
