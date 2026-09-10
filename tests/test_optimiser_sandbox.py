"""The optimiser's candidate-test harness, exercised against the real sandbox.

This is the gate that makes the skill miner more than a suggestion box: a
candidate skill is executed in the same sandbox the agent uses, over real rows,
and is only allowed to become a PR if it ran clean AND produced the columns the
golden's ``checkpoints.analysis.derived_cols`` asks for. Two fixture candidates
pin both verdicts — one that computes what it promised, one that raises.

Skipped when the data-agent environment is not installed (the repo-root CI job
has no pandas): the harness deliberately shells into
``services/data-agent`` rather than importing it, so its absence is a skip, not
a failure. No LLM call is involved anywhere here.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import optimiser_common as oc  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("uv") is None or not (oc.DATA_AGENT_DIR / "pyproject.toml").is_file(),
    reason="needs uv + services/data-agent (the sandbox's own environment)",
)

ROWS = [
    {"postcode": "2000", "total_weekly_rent": 1000.0, "n_rented": 2},
    {"postcode": "2000", "total_weekly_rent": 3000.0, "n_rented": 4},
    {"postcode": "2077", "total_weekly_rent": 900.0, "n_rented": 3},
]

# A candidate that does the weighted maths the house style demands.
GOOD = '''
@skill
def weighted_rent(df, group_col="postcode"):
    """Weighted average weekly rent per group (sum of rent over sum of bonds)."""
    grouped = df.groupby(group_col, as_index=False)[["total_weekly_rent", "n_rented"]].sum()
    bonds = grouped["n_rented"].replace(0, float("nan"))
    grouped["avg_rent"] = grouped["total_weekly_rent"] / bonds
    return grouped

out = weighted_rent(df)
print(out.to_string())
'''

# A candidate that blows up on the real column shape.
BAD = '''
@skill
def weighted_rent(df):
    """Broken: the extract has no `rent` column."""
    return df["rent"] / df["missing"]

out = weighted_rent(df)
'''


@pytest.fixture(scope="module", autouse=True)
def _sandbox_available() -> None:
    """Skip the module (rather than fail it) when the venv cannot be resolved."""
    probe = subprocess.run(
        ["uv", "run", "--extra", "llm", "python", "-c", "import pandas"],
        cwd=oc.DATA_AGENT_DIR,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        pytest.skip(f"data-agent env unavailable: {probe.stderr.strip()[-200:]}")


def test_a_good_candidate_passes_with_its_expected_column() -> None:
    outcome = oc.run_candidate_in_sandbox(
        code=oc.strip_skill_decorator(GOOD), rows=ROWS, expect_cols=["avg_rent"]
    )
    assert outcome.ok, outcome.as_dict()
    assert outcome.error is None
    assert "out" in [f["name"] for f in outcome.frames]


def test_a_good_candidate_still_fails_a_column_it_did_not_produce() -> None:
    """The derived_cols check is the half that catches a plausible-but-wrong skill."""
    outcome = oc.run_candidate_in_sandbox(
        code=oc.strip_skill_decorator(GOOD), rows=ROWS, expect_cols=["gross_yield_pct"]
    )
    assert not outcome.ok
    assert outcome.error is None
    assert outcome.missing_cols == ["gross_yield_pct"]


def test_a_broken_candidate_fails_with_its_traceback() -> None:
    outcome = oc.run_candidate_in_sandbox(
        code=oc.strip_skill_decorator(BAD), rows=ROWS, expect_cols=["avg_rent"]
    )
    assert not outcome.ok
    assert outcome.error
