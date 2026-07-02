"""Pure provider selection (Decision G).

No heavy imports here on purpose — this stays importable from the dependency-
light root test venv, same as nl2sql.py/sql_guardrails.py.
"""

from __future__ import annotations


def choose_provider(
    llm_provider: str, deepseek_key: str | None, anthropic_key: str | None
) -> tuple[str, str] | None:
    """Return (provider, api_key) for the configured LLM_PROVIDER, or None to use the stub.

    No cross-provider fallback: if the configured provider's key is missing, callers
    fall back to the offline stub rather than silently trying a different provider.
    """
    if llm_provider == "deepseek" and deepseek_key:
        return ("deepseek", deepseek_key)
    if llm_provider == "anthropic" and anthropic_key:
        return ("anthropic", anthropic_key)
    return None
