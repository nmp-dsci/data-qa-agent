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


def _validate_ast(cleaned: str) -> None:
    """AST check: exactly one top-level read query, no CTE-hidden DML/DDL."""
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


def validate_select(sql: str) -> str:
    """Allow exactly one read-only SELECT/CTE statement."""
    cleaned = _strip_sql_comments(sql).strip().rstrip(";").strip()
    if ";" in cleaned:
        raise UnsafeSQLError("Only a single statement is allowed")
    if not re.match(r"^\s*(select|with)\b", cleaned, re.IGNORECASE):
        raise UnsafeSQLError("Only SELECT queries are allowed")
    if _FORBIDDEN.search(cleaned):
        raise UnsafeSQLError("Query contains a disallowed keyword")
    _validate_ast(cleaned)
    return cleaned
