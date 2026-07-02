from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "data-agent"))

from agent.provider import choose_provider  # noqa: E402


def test_deepseek_selected_when_provider_and_key_present() -> None:
    assert choose_provider("deepseek", "ds-key", "an-key") == ("deepseek", "ds-key")


def test_anthropic_selected_when_configured() -> None:
    assert choose_provider("anthropic", "ds-key", "an-key") == ("anthropic", "an-key")


def test_no_cross_provider_fallback_when_configured_key_missing() -> None:
    # Configured for deepseek but no deepseek key -> stub, even though anthropic key exists.
    assert choose_provider("deepseek", None, "an-key") is None


def test_stub_when_no_key_for_configured_provider() -> None:
    assert choose_provider("anthropic", "ds-key", None) is None


def test_stub_for_unknown_provider() -> None:
    assert choose_provider("unknown", "ds-key", "an-key") is None


def test_stub_when_key_is_empty_string() -> None:
    assert choose_provider("deepseek", "", "an-key") is None
