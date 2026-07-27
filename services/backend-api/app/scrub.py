"""Right-sized PII scrubbing for the one field a user types (s32 W3, decision Q4).

The plan's constraint #4: the marts are **public NSW property records**, so there
is no PII in the data. The only PII surface is the free-text question, which is
persisted to ``app.messages.content`` and ``app.query_runs.question`` and shipped
to Logfire as a span attribute. That is a narrow surface, and a narrow surface
deserves a narrow tool — a regex pass plus Logfire's built-in scrubber, not a
Presidio pipeline whose false positives would corrupt the audit trail this app's
whole eval loop reads from.

**Conservative by design.** A false positive here is not harmless: it silently
rewrites a stored question, which is what goldens are promoted from and what
``scripts/inspect_run.py`` diagnoses. So each pattern has to be something no
property question would ever legitimately contain:

* **email** — unambiguous.
* **phone** — Australian mobile/landline shapes only, requiring the leading 0 or
  +61. A bare 8-digit run is NOT matched: prices, postcodes, and years are all
  bare digit runs, and matching them would eat real questions.
* **card** — 13-19 digits *and* a passing Luhn check. Luhn is the whole point:
  without it, "properties between 1000000 and 2000000" is a card number.
* **TFN** — 8-9 digits with the ATO weighted checksum. Same reasoning: the
  checksum is what separates a tax file number from a price.

Everything else is left alone deliberately. A street address in a question is
not PII in this app — it is the question.
"""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")

# Australian phone shapes: +61 4xx xxx xxx, 04xx xxx xxx, (02) xxxx xxxx.
# Anchored on a leading 0 or +61 so a bare digit run (a price, a postcode, a
# year) can never match.
PHONE_RE = re.compile(
    r"(?<![\w.])(?:\+61[\s-]?|0)(?:[23478])(?:[\s-]?\d){8}(?![\w.])",
)

# Candidate card/TFN digit runs, checksum-verified below rather than replaced on
# sight. Separators allowed, since people paste them.
_DIGIT_RUN_RE = re.compile(r"(?<![\w.])(\d[\d\s-]{6,21}\d)(?![\w.])")

EMAIL_MASK = "[email]"
PHONE_MASK = "[phone]"
CARD_MASK = "[card]"
TFN_MASK = "[tfn]"


def _luhn_ok(digits: str) -> bool:
    """The card checksum. Rejects ordinary numbers that happen to be 16 digits."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


# The ATO's tax-file-number weighting. A TFN is 8 or 9 digits whose weighted sum
# divides by 11 — cheap to check and vanishingly unlikely to fire by accident.
_TFN_WEIGHTS = (1, 4, 3, 7, 5, 8, 6, 9, 10)


def _tfn_ok(digits: str) -> bool:
    if len(digits) not in (8, 9):
        return False
    weights = _TFN_WEIGHTS[: len(digits)]
    return sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11 == 0


def _mask_digit_runs(text: str) -> str:
    """Replace only those digit runs that pass a card or TFN checksum."""

    def replace(match: re.Match[str]) -> str:
        raw = match.group(1)
        digits = re.sub(r"\D", "", raw)
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            return CARD_MASK
        if _tfn_ok(digits):
            return TFN_MASK
        return raw

    return _DIGIT_RUN_RE.sub(replace, text)


def scrub_text(text: str | None) -> str | None:
    """Mask clear PII in a free-text string; return it unchanged otherwise.

    Order matters: emails first (an email can contain digits that would otherwise
    be examined), then phones, then checksum-verified digit runs.
    """
    if not text:
        return text
    out = EMAIL_RE.sub(EMAIL_MASK, text)
    out = PHONE_RE.sub(PHONE_MASK, out)
    return _mask_digit_runs(out)


def found_pii(text: str | None) -> bool:
    """Whether scrubbing would change this text — used to count, not to gate."""
    return bool(text) and scrub_text(text) != text
