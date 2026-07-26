from __future__ import annotations

import re

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|merge|call|into)\b",
    re.IGNORECASE,
)

# AST-based defense-in-depth (over the regex denylist and the read-only agent_ro
# role). The SQL editor turns arbitrary user-typed SQL into a first-class input,
# so we parse it and reject anything that isn't a single top-level read query —
# in particular DML/DDL hidden inside a CTE (e.g. `WITH x AS (DELETE ... RETURNING
# *) SELECT * FROM x`), which the regex/read-only role catch, but which we want
# rejected explicitly and early. Optional import so the module still loads (and
# the regex + read-only role still guard) if sqlglot isn't installed.
try:
    import sqlglot
    from sqlglot import exp

    _SQLGLOT_AVAILABLE = True
    # Node types that mutate data/schema or run privileged commands. sqlglot
    # models the ones it recognises (Insert/Update/Delete/…); everything it
    # doesn't (VACUUM, SET, etc.) parses to exp.Command, which we also forbid.
    _FORBIDDEN_NODES = tuple(
        getattr(exp, name)
        for name in (
            "Insert",
            "Update",
            "Delete",
            "Merge",
            "Create",
            "Drop",
            "Alter",
            "AlterTable",
            "TruncateTable",
            "Grant",
            "Command",
        )
        if hasattr(exp, name)
    )
except ImportError:  # pragma: no cover - sqlglot is a declared dependency
    _SQLGLOT_AVAILABLE = False

# Functions that are read-only in *form* but not in effect, so no node-type check
# catches them: `SELECT set_config(...)` parses as a perfectly ordinary Select.
# set_config is the important one — ``app.current_user_id`` is the session
# variable every RLS policy reads, so a query able to rewrite it is a query able
# to choose whose rows it sees (s32 W3 found this; the read-only role does not
# stop it, because set_config needs no write privilege). The rest are the usual
# filesystem/network/sleep escapes. `current_setting` is deliberately absent —
# reading the context is fine, writing it is not.
_FORBIDDEN_FUNCTIONS = frozenset(
    {
        "set_config",
        "pg_reload_conf",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_stat_file",
        "pg_ls_dir",
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "lo_import",
        "lo_export",
        "dblink",
        "dblink_exec",
        "dblink_connect",
        "query_to_xml",
        "xmltable",
    }
)


class UnsafeSQLError(ValueError):
    """Raised when generated SQL is not a single read-only SELECT."""


def _strip_sql_comments(sql: str) -> str:
    """Remove line/block comments while preserving quoted string contents."""
    out: list[str] = []
    i = 0
    in_single = False
    in_double = False
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""

        if in_single:
            out.append(ch)
            if ch == "'" and nxt == "'":
                out.append(nxt)
                i += 2
                continue
            if ch == "'":
                in_single = False
            i += 1
            continue

        if in_double:
            out.append(ch)
            if ch == '"' and nxt == '"':
                out.append(nxt)
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue

        if ch == "'":
            in_single = True
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            out.append(ch)
            i += 1
            continue
        if ch == "-" and nxt == "-":
            i += 2
            while i < len(sql) and sql[i] not in "\r\n":
                i += 1
            out.append("\n")
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < len(sql) and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i = min(i + 2, len(sql))
            out.append(" ")
            continue

        out.append(ch)
        i += 1
    return "".join(out)


def _function_name(node: object) -> str:
    """The lowercase called-function name for a node, or "" if it isn't a call.

    Covers both shapes sqlglot produces: a modelled function (``exp.Func``
    subclasses expose ``sql_name()``) and an unrecognised one, which lands as
    ``exp.Anonymous`` with the name in ``this``.
    """
    if isinstance(node, exp.Anonymous):
        return str(node.this or "").lower()
    if isinstance(node, exp.Func):
        try:
            return str(node.sql_name()).lower()
        except Exception:  # noqa: BLE001 — an unnameable node is not a match
            return ""
    return ""


def _blank_quoted(sql: str) -> str:
    """Replace the *contents* of string literals and quoted identifiers with spaces.

    The keyword denylist below scans text, and text includes the data. A NSW
    address of ``'GRANT ST'`` (which is in the committed sample) made
    ``\\bgrant\\b`` match and the guard refuse an ordinary WHERE clause — the
    guard reading a value as a command. Blanking quoted spans first fixes that
    without weakening anything: a keyword inside a literal is data by definition
    and can never execute, so nothing dangerous can hide there.

    Structure is preserved — the quotes themselves stay, and so does every
    semicolon and paren outside them — so the single-statement check still sees
    the real shape of the query. ``''`` and ``""`` escapes are handled, so a
    literal containing a quote can't terminate the span early and expose its tail
    to the scanner.
    """
    out: list[str] = []
    i = 0
    quote = ""
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if quote:
            if ch == quote and nxt == quote:  # an escaped quote inside the literal
                out.append("  ")
                i += 2
                continue
            if ch == quote:
                quote = ""
                out.append(ch)
            else:
                # Keep newlines so line/column positions stay recognisable.
                out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _validate_ast(cleaned: str) -> None:
    """AST check: one top-level read query, no CTE-hidden DML/DDL, no escapes."""
    if not _SQLGLOT_AVAILABLE:
        return
    try:
        statements = [s for s in sqlglot.parse(cleaned, dialect="postgres") if s is not None]
    except Exception as exc:  # noqa: BLE001 — a parse failure is a rejection
        raise UnsafeSQLError(f"Could not parse SQL: {exc}") from exc
    if len(statements) != 1:
        raise UnsafeSQLError("Only a single statement is allowed")
    root = statements[0]
    if not isinstance(root, exp.Query):
        raise UnsafeSQLError("Only SELECT queries are allowed")
    # Walk the whole tree so DML/DDL hidden inside a CTE (which leaves the root a
    # Select) is still rejected — the read-only role is the backstop, this is the
    # explicit early guard.
    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise UnsafeSQLError("Query contains a disallowed statement")
        name = _function_name(node)
        if name and name in _FORBIDDEN_FUNCTIONS:
            raise UnsafeSQLError(f"Query calls a disallowed function: {name}")


def validate_select(sql: str) -> str:
    """Allow exactly one read-only SELECT/CTE statement.

    Three layers, cheapest first: a shape check (starts with SELECT/WITH, one
    statement), a keyword denylist over the *code* only, then the AST walk. The
    denylist runs against ``_blank_quoted`` output so a value is never mistaken
    for a command, while the returned SQL is the comment-stripped original —
    blanking is for inspection, never for execution.
    """
    cleaned = _strip_sql_comments(sql).strip().rstrip(";").strip()
    code_only = _blank_quoted(cleaned)
    if ";" in code_only:
        raise UnsafeSQLError("Only a single statement is allowed")
    if not re.match(r"^\s*(select|with)\b", cleaned, re.IGNORECASE):
        raise UnsafeSQLError("Only SELECT queries are allowed")
    if _FORBIDDEN.search(code_only):
        raise UnsafeSQLError("Query contains a disallowed keyword")
    _validate_ast(cleaned)
    return cleaned
