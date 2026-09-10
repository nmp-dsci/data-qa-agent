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
    """Champion v3 / challenger v4, challenger clears the pass_rate + flips gate."""
    monkeypatch.setattr(
        mc, "get_alias_version", lambda name, alias: {"champion": "3", "challenger": "4"}[alias]
    )
    monkeypatch.setattr(
        reg, "_version_info", lambda version: {"agent_version_id": f"av-{version}"}
    )
    monkeypatch.setattr(
        reg,
        "_latest_eval",
        lambda avid: {"id": f"run-{avid}", "pack_version": "p1", "totals": {"pass_rate": 1.0}},
    )
    monkeypatch.setattr(reg, "_passed_cases", lambda run_id: set())
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

    reg.cmd_promote(argparse.Namespace())

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

    reg.cmd_promote(argparse.Namespace())

    assert len(_stub_promote_inputs) == 1
    assert "could not clear" not in capsys.readouterr().err
