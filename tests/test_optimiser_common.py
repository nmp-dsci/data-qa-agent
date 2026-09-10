"""Unit tests for the offline optimiser's shared machinery (s49 M3, W-D).

Deterministic and LLM-free by construction: the Agent SDK is a stub object, the
database is never touched, and nothing here shells out. The one test that does
run real code in the sandbox lives in ``test_optimiser_sandbox.py`` and skips
itself when the data-agent environment is not present.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import optimiser_common as oc  # noqa: E402
import reflect as reflect_script  # noqa: E402
import skill_miner  # noqa: E402


def _signal(need: str, run_id: str = "r1", **kw: Any) -> oc.GapSignal:
    return oc.GapSignal(run_id=run_id, question="q", need=need, **kw)


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


def test_normalise_need_drops_stopwords_and_punctuation() -> None:
    assert oc.normalise_need("Compute the yield of a postcode!") == "compute yield postcode"


def test_cluster_groups_paraphrases_and_ranks_by_size() -> None:
    signals = [
        _signal("compute blended postcode yield then rank top N", "a"),
        _signal("blended yield per postcode, ranked top N", "b"),
        _signal("compute the blended yield for postcodes and rank", "c"),
        _signal("seasonally adjust a monthly series", "d"),
    ]
    clusters = oc.cluster_needs(signals)
    assert [c.size for c in clusters] == [3, 1]
    assert clusters[0].run_ids == ["a", "b", "c"]
    assert "seasonal" in clusters[1].label


def test_cluster_keeps_unrelated_needs_apart() -> None:
    clusters = oc.cluster_needs(
        [_signal("rolling median of weekly rent", "a"), _signal("choropleth colour bins", "b")]
    )
    assert len(clusters) == 2


def test_cluster_of_nothing_is_empty() -> None:
    assert oc.cluster_needs([]) == []


# ---------------------------------------------------------------------------
# Trace reading
# ---------------------------------------------------------------------------


def test_sandbox_code_from_trace_strips_imports_and_takes_first_pass() -> None:
    trace = [
        {"kind": "sql", "status": "ok"},
        {
            "kind": "model",
            "tool_calls": [
                {"name": "extract", "args": "{}"},
                {
                    "name": "run_analysis",
                    "args": json.dumps({"code": "import pandas as pd\nx = 1"}),
                },
            ],
        },
        {"kind": "model", "tool_calls": [{"name": "run_analysis", "args": {"code": "y = 2"}}]},
    ]
    assert oc.sandbox_code_from_trace(trace) == "x = 1"


def test_sandbox_code_from_trace_tolerates_junk() -> None:
    assert oc.sandbox_code_from_trace(None) == ""
    no_args = [{"kind": "model", "tool_calls": [{"name": "run_analysis"}]}]
    assert oc.sandbox_code_from_trace(no_args) == ""
    assert oc.sandbox_code_from_trace({"steps": [{"kind": "analysis"}]}) == ""


def test_analysis_steps_handles_both_trace_shapes() -> None:
    step = {"kind": "analysis", "skills_used": ["growth_rate"]}
    assert oc.analysis_steps([step]) == [step]
    assert oc.analysis_steps({"steps": [step]}) == [step]
    assert oc.analysis_steps(None) == []


# ---------------------------------------------------------------------------
# Parsing the model's reply
# ---------------------------------------------------------------------------

REPLY = """Here is the skill.

```json
{"name": "blended_yield", "slug": "blended-yield", "expect_cols": ["gross_yield_pct"]}
```

```python
@skill
def blended_yield(df):
    return df
```

```python
import pandas as pd
def test_it():
    assert True
```
"""


def test_first_json_block_and_python_blocks() -> None:
    meta = oc.first_json_block(REPLY)
    assert meta["name"] == "blended_yield"
    blocks = oc.python_blocks(REPLY)
    assert len(blocks) == 2
    assert blocks[0].startswith("@skill")
    assert "def test_it" in blocks[1]


def test_first_json_block_skips_unparseable_json() -> None:
    assert oc.first_json_block("```json\n{not json}\n```") == {}
    assert oc.first_json_block("no blocks at all") == {}


def test_strip_skill_decorator_leaves_the_function() -> None:
    src = oc.strip_skill_decorator("@skill\ndef f():\n    return 1\n")
    assert src == "def f():\n    return 1"
    assert oc.strip_skill_decorator("@skills.skill\ndef f(): ...").startswith("def f")


def test_slugify_is_branch_safe() -> None:
    assert oc.slugify("Blended yield, ranked top N!") == "blended-yield-ranked-top-n"
    assert oc.slugify("") == "candidate"
    assert len(oc.slugify("x" * 100)) <= 40


# ---------------------------------------------------------------------------
# PR body
# ---------------------------------------------------------------------------


def test_render_pr_body_lists_evidence_files_and_test() -> None:
    body = oc.render_pr_body(
        kind="skill",
        hypothesis="No skill computed a blended yield.",
        signals=[
            _signal("blended yield", "abcdef1234", why="gross_yield wants two frames"),
            _signal("blended yield", "beefcafe99", source="judge", case_key="nsw_rent-x"),
        ],
        files=["services/data-agent/agent/skills/analysis.py"],
        test_result={"ok": True, "missing_cols": []},
        case_keys=["nsw_rent-x"],
        eval_run_id="run-1",
    )
    assert "Offline optimiser — skill" in body
    assert "No skill computed a blended yield." in body
    assert "`abcdef12`" in body and "`beefcafe`" in body
    assert "`nsw_rent-x`" in body
    assert "run-1" in body
    assert "services/data-agent/agent/skills/analysis.py" in body
    assert '"ok": true' in body
    assert "skill_miner.py" in body


def test_render_pr_body_escapes_pipes_so_the_table_survives() -> None:
    body = oc.render_pr_body(
        kind="skill",
        hypothesis="h",
        signals=[_signal("a | b", "r1", why="x\ny")],
        files=[],
    )
    assert "a \\| b" in body
    assert "x y" in body
    assert "- (none)" in body


def test_render_pr_body_reflect_names_the_other_script() -> None:
    body = oc.render_pr_body(kind="reflect", hypothesis="h", signals=[], files=["k.md"])
    assert "reflect.py" in body
    assert "Evidence" in body


# ---------------------------------------------------------------------------
# The SDK bridge (stubbed — never a real model call)
# ---------------------------------------------------------------------------


class _Block:
    def __init__(self, text: str) -> None:
        self.text = text


class _Msg:
    def __init__(self, *texts: str) -> None:
        self.content = [_Block(t) for t in texts]


class _StubSdk:
    """Just enough of ``claude_agent_sdk`` for ``ask_opus``."""

    def __init__(self) -> None:
        self.options: Any = None

    def ClaudeAgentOptions(self, **kw: Any) -> dict[str, Any]:  # noqa: N802 — mirrors the SDK
        self.options = kw
        return kw

    def query(self, *, prompt: str, options: Any) -> Any:
        self.prompt = prompt

        async def _gen() -> Any:
            yield _Msg("part one")
            yield object()  # a message with no .content — must be skipped
            yield _Msg("part two")

        return _gen()


def test_ask_opus_concatenates_text_and_locks_down_the_toolset() -> None:
    sdk = _StubSdk()
    out = oc.ask_opus(system_prompt="sys", prompt="hi", sdk=sdk)
    assert out == "part one\npart two"
    assert sdk.options["model"] == oc.OPUS_MODEL
    assert sdk.options["allowed_tools"] == ["Read", "Grep", "Glob"]
    assert sdk.options["tools"] == ["Read", "Grep", "Glob"]
    # No write tool may leak in — the worktree guarantee depends on it.
    assert not {"Write", "Edit", "Bash"} & set(sdk.options["allowed_tools"])
    assert sdk.options["env"]["ANTHROPIC_API_KEY"] == ""


def test_libpq_url_strips_the_async_driver() -> None:
    assert oc._libpq_url("postgresql+asyncpg://u:p@db:5432/x") == "postgresql://u:p@db:5432/x"


def test_load_dotenv_reads_and_lets_the_shell_win(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    env = tmp_path / ".env"
    env.write_text("# comment\nAPI_HOST_PORT=8010\nQUOTED='x'\nbroken\n", encoding="utf-8")
    assert oc.load_dotenv(env)["API_HOST_PORT"] == "8010"
    assert oc.load_dotenv(env)["QUOTED"] == "x"
    monkeypatch.setenv("API_HOST_PORT", "9999")
    assert oc.load_dotenv(env)["API_HOST_PORT"] == "9999"


def test_rows_as_dicts_handles_both_row_shapes() -> None:
    payload = {"columns": [{"name": "a"}, "b"], "rows": [[1, 2], {"a": 3, "b": 4}]}
    assert oc.rows_as_dicts(payload) == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]


# ---------------------------------------------------------------------------
# skill_miner: signal extraction
# ---------------------------------------------------------------------------


def test_case_signals_reads_gaps_judge_and_checkpoints() -> None:
    case = {
        "case_key": "nsw_rent-x",
        "dataset": "nsw_rent",
        "judge": {"label": "low", "diagnosis": "analysis", "reason": "wrong denominator"},
        "checkpoints": {"analysis": {"score": 0.5, "missing_skills": ["blended_yield"]}},
    }
    run = {
        "question": "yields?",
        "trace": [
            {
                "kind": "analysis",
                "skill_gaps": [{"need": "blended yield", "why": "no skill"}],
                "used_inline_math": True,
            }
        ],
    }
    signals = skill_miner._case_signals("run-1", case, run)
    assert [s.source for s in signals] == ["skill_gap", "inline_math", "judge", "checkpoint"]
    assert all(s.case_key == "nsw_rent-x" and s.dataset == "nsw_rent" for s in signals)
    assert "blended_yield" in signals[3].need


def test_case_signals_tolerates_missing_judge_and_checkpoints() -> None:
    signals = skill_miner._case_signals("r", {"case_key": "k"}, {"trace": []})
    assert signals == []


def test_case_signals_ignores_a_passing_checkpoint() -> None:
    case = {"checkpoints": {"analysis": {"score": 1.0}}}
    assert skill_miner._case_signals("r", case, {"trace": []}) == []


def test_expected_cols_prefers_the_case_checkpoints() -> None:
    cluster = oc.Cluster(label="l", signals=[_signal("n", "r1", case_key="k")])
    cases = {"r1": {"case_checkpoints": {"analysis": {"derived_cols": ["gross_yield_pct"]}}}}
    assert skill_miner.expected_cols(cluster, cases, {"expect_cols": ["other"]}) == [
        "gross_yield_pct"
    ]
    assert skill_miner.expected_cols(cluster, {}, {"expect_cols": ["other"]}) == ["other"]


def test_test_candidate_refuses_without_rows_or_snippet() -> None:
    assert not skill_miner.test_candidate(
        skill_src="def f(): ...", meta={}, rows=[{"a": 1}], expect_cols=[]
    ).ok
    outcome = skill_miner.test_candidate(
        skill_src="def f(): ...", meta={"sandbox_test": "f()"}, rows=[], expect_cols=[]
    )
    assert not outcome.ok and "truth rows" in (outcome.error or "")


def test_test_candidate_refuses_with_no_expectation_even_if_it_would_run() -> None:
    """A candidate that merely doesn't crash is not proven (review-3, D4)."""
    outcome = skill_miner.test_candidate(
        skill_src="def f(): ...",
        meta={"sandbox_test": "f()"},
        rows=[{"a": 1}],
        expect_cols=[],
    )
    assert not outcome.ok
    assert "no expectation" in (outcome.error or "")


def test_apply_candidate_appends_the_skill_and_writes_a_test(tmp_path: Path) -> None:
    skills = tmp_path / skill_miner.SKILLS_FILE
    skills.parent.mkdir(parents=True)
    (tmp_path / "services/data-agent/tests").mkdir(parents=True)
    skills.write_text("existing = 1\n", encoding="utf-8")

    files = skill_miner.apply_candidate(
        tmp_path, skill_src="@skill\ndef f():\n    ...", test_src="def test_f(): ...", name="f"
    )
    assert files == [skill_miner.SKILLS_FILE, "services/data-agent/tests/test_skill_f.py"]
    body = skills.read_text(encoding="utf-8")
    assert body.startswith("existing = 1")
    assert body.endswith("@skill\ndef f():\n    ...\n")
    assert (tmp_path / files[1]).read_text(encoding="utf-8") == "def test_f(): ...\n"


# ---------------------------------------------------------------------------
# reflect: the edit surface
# ---------------------------------------------------------------------------


def test_check_target_allows_prompt_and_knowledge_only() -> None:
    assert reflect_script.check_target("services/data-agent/knowledge/nsw_rent.md")
    assert reflect_script.check_target(
        "./services/data-agent/agent/prompts/workspace_claude.md"
    ).startswith("services/")
    for bad in (
        "services/data-agent/agent/skills/analysis.py",
        "evals/cases/nsw_rent.yaml",
        "README.md",
        "services/data-agent/knowledge/../../main.py",
    ):
        with pytest.raises(reflect_script.EditError):
            reflect_script.check_target(bad)


def test_apply_edits_replaces_exactly_once(tmp_path: Path) -> None:
    rel = "services/data-agent/knowledge/rent.md"
    page = tmp_path / rel
    page.parent.mkdir(parents=True)
    page.write_text("Rent is a mean.\nOther line.\n", encoding="utf-8")
    reflect_script.apply_edits(
        tmp_path, rel, [{"old": "Rent is a mean.", "new": "Rent is weighted."}]
    )
    assert page.read_text(encoding="utf-8") == "Rent is weighted.\nOther line.\n"


def test_apply_edits_refuses_ambiguous_or_missing_text(tmp_path: Path) -> None:
    rel = "services/data-agent/knowledge/rent.md"
    page = tmp_path / rel
    page.parent.mkdir(parents=True)
    page.write_text("dup\ndup\n", encoding="utf-8")
    with pytest.raises(reflect_script.EditError, match="appears 2 times"):
        reflect_script.apply_edits(tmp_path, rel, [{"old": "dup", "new": "x"}])
    with pytest.raises(reflect_script.EditError, match="appears 0 times"):
        reflect_script.apply_edits(tmp_path, rel, [{"old": "absent", "new": "x"}])
    with pytest.raises(reflect_script.EditError, match="no `old`"):
        reflect_script.apply_edits(tmp_path, rel, [{"new": "x"}])
    # The file is left untouched by a failed edit.
    assert page.read_text(encoding="utf-8") == "dup\ndup\n"


def test_apply_edits_needs_the_file_to_exist(tmp_path: Path) -> None:
    with pytest.raises(reflect_script.EditError, match="does not exist"):
        reflect_script.apply_edits(tmp_path, "services/data-agent/knowledge/nope.md", [])


def test_apply_edits_refuses_a_path_that_resolves_outside_the_worktree(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    (worktree / "services/data-agent/knowledge").mkdir(parents=True)
    outside = tmp_path / "evil.md"
    outside.write_text("secret\n", encoding="utf-8")
    rel = "services/data-agent/knowledge/../../../../evil.md"
    with pytest.raises(reflect_script.EditError, match="escapes the worktree|outside the allowed"):
        reflect_script.apply_edits(worktree, rel, [{"old": "secret", "new": "x"}])
    assert outside.read_text(encoding="utf-8") == "secret\n"


def test_reflect_prompt_carries_the_diagnosis_and_the_code(tmp_path: Path) -> None:
    prompt = reflect_script.build_prompt(
        [
            {
                "case_key": "nsw_rent-x",
                "question": "why?",
                "judge": {"label": "low", "diagnosis": "knowledge", "reason": "wrong mart"},
                "checkpoints": {"sql": {"score": 0.2}},
                "sql_text": "select 1",
                "trace": [
                    {
                        "kind": "model",
                        "tool_calls": [{"name": "run_analysis", "args": {"code": "z=1"}}],
                    },
                    {"kind": "analysis", "skills_used": ["growth_rate"], "skill_gaps": []},
                ],
            }
        ],
        "run-9",
    )
    assert "run-9" in prompt
    assert "diagnosis=knowledge" in prompt
    assert "wrong mart" in prompt
    assert "z=1" in prompt
    assert "growth_rate" in prompt


def test_ensure_worktrees_ignored_is_idempotent(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(oc, "REPO_ROOT", tmp_path)
    (tmp_path / ".gitignore").write_text("node_modules\n", encoding="utf-8")
    oc.ensure_worktrees_ignored()
    oc.ensure_worktrees_ignored()
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").count(".worktrees/") == 1
