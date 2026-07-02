from __future__ import annotations

import re

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|merge|call|into)\b",
    re.IGNORECASE,
)


class UnsafeSQLError(ValueError):
    """Raised when generated SQL is not a single read-only SELECT."""


def validate_select(sql: str) -> str:
    """Allow exactly one read-only SELECT/CTE statement."""
    cleaned = sql.strip().rstrip(";").strip()
    if ";" in cleaned:
        raise UnsafeSQLError("Only a single statement is allowed")
    if not re.match(r"^\s*(select|with)\b", cleaned, re.IGNORECASE):
        raise UnsafeSQLError("Only SELECT queries are allowed")
    if _FORBIDDEN.search(cleaned):
        raise UnsafeSQLError("Query contains a disallowed keyword")
    return cleaned
