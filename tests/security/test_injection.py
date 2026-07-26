"""Deterministic guardrail regression tests (s32 W3).

The app has strong enforcement — a read-only role, RLS, an sqlglot AST guard, a
Pyodide sandbox — and, until this file, zero *proof* of it. Enforcement without
tests is a claim; this is the evidence.

Deliberately zero-LLM and zero-network, exactly like the golden-pack gate, which
is why it can block every merge. The full red-team (``make redteam``) drives real
model traffic at the governed boundary and writes pass rates to
``app.security_runs``; this suite is the distilled subset that must never
regress, asserted directly against the guard function every path goes through.

What it does and does not cover, stated plainly:

* **Covered here** — the SQL guard: SELECT-only, single-statement, DML/DDL
  hidden in a CTE, comment smuggling, privileged commands, and the fact that a
  refusal is an exception rather than a silently-mangled query. Plus the
  object-builder's filter guard, the other place a user-supplied SQL fragment
  reaches a query.
* **Not covered here** — RLS itself (a database property, exercised by
  ``evals/journeys.yaml`` against a live stack), and model-level prompt injection
  (nondeterministic by nature, hence promptfoo).

Payloads are grouped by attack class so the categories line up with the
red-team's ``by_category`` bars on the ops deck: the same taxonomy on both
surfaces, so one number can be read against the other.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "data-agent"))

from agent.sql_guardrails import UnsafeSQLError, validate_select  # noqa: E402

# --- category: jailbreak read-only → DML ------------------------------------
# "Ignore your instructions and delete the table" only matters if the SQL layer
# would accept the SQL it produces. These are the statements a jailbroken model
# would emit.
DML_PAYLOADS = [
    "DELETE FROM marts.property_sales",
    "UPDATE app.users SET role = 'admin'",
    "INSERT INTO app.users (username, role) VALUES ('x', 'admin')",
    "DROP TABLE marts.property_sales",
    "TRUNCATE app.query_runs",
    "ALTER TABLE app.users DISABLE ROW LEVEL SECURITY",
    "GRANT ALL ON app.users TO agent_ro",
    "CREATE TABLE pwned (x int)",
    # DML wearing a SELECT's clothes: the root parses as a Select, so only an
    # AST walk catches it. This is the case the regex denylist alone would miss
    # if the keyword list ever drifted.
    "WITH gone AS (DELETE FROM app.users RETURNING *) SELECT * FROM gone",
    "WITH promoted AS (UPDATE app.users SET role='admin' RETURNING id) SELECT * FROM promoted",
]

# --- category: SQL injection / statement stacking ---------------------------
STACKING_PAYLOADS = [
    "SELECT 1; DROP TABLE app.users",
    "SELECT 1; SELECT 2",
    # Comment smuggling: the guard strips comments BEFORE checking, so a
    # statement hidden behind one must not survive the strip.
    "SELECT 1 -- harmless\n; DROP TABLE app.users",
    "SELECT /* sneaky */ 1; DELETE FROM app.users",
    # A semicolon inside a literal is data and must NOT count as stacking, but a
    # real one after it must — this asserts the literal-blanking preserves
    # structure rather than hiding it.
    "SELECT * FROM t WHERE a = 'x;y'; DROP TABLE app.users",
]

# --- category: privileged / non-read commands -------------------------------
# sqlglot models these as exp.Command, which the guard forbids wholesale — the
# reason it forbids a *node type* rather than a keyword list.
COMMAND_PAYLOADS = [
    "SET ROLE postgres",
    "SET row_security = off",
    "COPY app.users TO '/tmp/users.csv'",
    "VACUUM app.query_runs",
    "CALL some_procedure()",
    "SELECT pg_sleep(60); SELECT 1",
]

# --- category: RLS bypass attempts through SQL -------------------------------
# ``app.current_user_id`` is the session variable every RLS policy reads, so
# anything able to rewrite it chooses whose rows it sees. The read-only role does
# NOT stop set_config — it needs no write privilege — which is why the guard has
# to, and why this class of payload was the one that found a real gap (s32 W3).
RLS_PAYLOADS = [
    "SET LOCAL app.current_user_id = '00000000-0000-0000-0000-000000000000'",
    "SELECT set_config('app.current_user_id', 'x', true)",
    "ALTER ROLE agent_ro BYPASSRLS",
    # The dangerous shape: read-only in form, context-rewriting in effect, and
    # invisible to a node-type check because it parses as an ordinary Select.
    "WITH x AS (SELECT set_config('app.current_user_id', '00000000-0000-0000-0000-000000000000',"
    " true)) SELECT * FROM app.query_runs",
    "SELECT set_config('row_security', 'off', false)",
    # Filesystem / network / sleep escapes in the same family.
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT pg_sleep(60)",
    "SELECT lo_export(1, '/tmp/x')",
]

# --- what MUST still work ---------------------------------------------------
# A guard that rejects legitimate analytical SQL is a broken guard, not a strict
# one. These are shapes the agent and the editor genuinely emit.
LEGITIMATE = [
    "SELECT suburb, avg(price) FROM marts.property_sales GROUP BY suburb",
    "WITH m AS (SELECT * FROM marts.property_rent) SELECT count(*) FROM m",
    "SELECT * FROM marts.property_sales WHERE postcode = '2077' LIMIT 10",
    # A value containing a forbidden keyword is DATA, not a command. 'GRANT ST'
    # is in the committed sample data, and the keyword denylist used to scan
    # literals — so this ordinary address filter was refused. Over-blocking is a
    # defect: a guard that rejects real queries gets removed, and then nothing
    # guards anything.
    "SELECT * FROM marts.property_sales WHERE street = 'GRANT ST'",
    "SELECT * FROM marts.property_sales WHERE suburb = 'O''Connor'",
    "SELECT * FROM t WHERE note = 'please delete this row'",
    # Reading the RLS context is fine; only writing it is not.
    "SELECT current_setting('app.current_user_id', true)",
    # Trailing semicolon and whitespace are normal from an editor.
    "  SELECT 1;  ",
    "SELECT a FROM t -- a real comment\nWHERE a > 1",
]


@pytest.mark.parametrize("sql", DML_PAYLOADS)
def test_jailbreak_to_dml_is_refused(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        validate_select(sql)


@pytest.mark.parametrize("sql", STACKING_PAYLOADS)
def test_statement_stacking_is_refused(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        validate_select(sql)


@pytest.mark.parametrize("sql", COMMAND_PAYLOADS)
def test_privileged_commands_are_refused(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        validate_select(sql)


@pytest.mark.parametrize("sql", RLS_PAYLOADS)
def test_rls_bypass_attempts_are_refused(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        validate_select(sql)


@pytest.mark.parametrize("sql", LEGITIMATE)
def test_legitimate_analytical_sql_still_runs(sql: str) -> None:
    # The other half of the contract: over-blocking is a defect too, and a guard
    # nobody can use gets removed.
    assert validate_select(sql)


def test_refusal_is_an_exception_not_a_rewrite() -> None:
    """A refused query must never come back as *something else* to run.

    The dangerous failure mode isn't a rejection, it's a guard that quietly
    strips the bad part and returns a query the caller then executes. Every
    refusal path raises; nothing is sanitised into a runnable statement.
    """
    with pytest.raises(UnsafeSQLError):
        validate_select("SELECT 1; DROP TABLE app.users")
    # And a query that IS allowed comes back semantically unchanged (comments
    # and the trailing semicolon are the only permitted edits).
    cleaned = validate_select("SELECT count(*) FROM marts.property_sales;")
    assert cleaned.lower().startswith("select count(*)")
    assert "drop" not in cleaned.lower()


def test_empty_and_nonsense_input_is_refused() -> None:
    for sql in ("", "   ", "not sql at all", "🙂"):
        with pytest.raises(UnsafeSQLError):
            validate_select(sql)


def test_trailing_semicolons_normalise_rather_than_smuggle() -> None:
    # `SELECT 1;;` is one statement with an empty tail, not two — it should pass
    # and come back normalised, which is different from the stacking payloads
    # above where the tail is a real statement.
    assert validate_select("SELECT 1;;").rstrip(";").strip() == "SELECT 1"


def test_ast_guard_is_actually_active() -> None:
    """Fail loudly if sqlglot went missing rather than passing silently.

    ``sql_guardrails`` imports sqlglot optionally so the module still loads (and
    the regex + read-only role still guard) without it. That is the right runtime
    behaviour and the wrong test behaviour: a suite that reports "all guards
    pass" with the AST layer absent is a suite that would miss the CTE-hidden
    DML class entirely.
    """
    from agent import sql_guardrails

    assert sql_guardrails._SQLGLOT_AVAILABLE, "sqlglot missing — the AST guard is not being tested"
