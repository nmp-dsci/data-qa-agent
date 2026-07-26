"""PII scrub: catches the real thing, leaves property questions alone (s32 W3).

Both halves matter equally, and the second is the one that would actually hurt.
A false positive silently rewrites a stored question — and stored questions are
what goldens are promoted from (``/admin/eval-goldens/from-run``) and what
``scripts/inspect_run.py`` diagnoses. Over-scrubbing would corrupt the eval loop's
source material, which is why the card and TFN patterns are checksum-gated
instead of "any long digit run".
"""

from __future__ import annotations

from app.scrub import found_pii, scrub_text

# Property questions this app actually receives. Every one is full of digits —
# prices, postcodes, years, bedroom counts — and none of it is PII.
REAL_QUESTIONS = [
    "show me trend of sale price for houses for Normanhurst vs Hornsby 2010 to 2026",
    "Which suburbs in postcode 2077 have the highest rent growth?",
    "Properties between 1000000 and 2000000 in the last 12 months",
    "What was the median price in 2019 for 3 bedroom houses?",
    "Compare 2076 and 2077 for gross yield",
    "Show sales on GRANT ST",
    "How many sales between 850000 and 1250000 happened in 2024?",
    # A long, plainly-not-a-card digit run.
    "filter to reference 12345678901234567",
]


def test_real_questions_are_never_rewritten() -> None:
    for question in REAL_QUESTIONS:
        assert scrub_text(question) == question, question
        assert not found_pii(question), question


def test_email_is_masked() -> None:
    assert scrub_text("email me at nathan.phillips@example.com") == "email me at [email]"


def test_australian_phone_shapes_are_masked() -> None:
    for raw in ("0412 345 678", "0412345678", "+61 412 345 678", "0298765432"):
        out = scrub_text(f"call {raw} about the listing")
        assert "[phone]" in out, raw
        assert raw not in out, raw


def test_a_card_number_needs_a_valid_checksum() -> None:
    # A real (test) Visa number — passes Luhn, so it is masked.
    assert "[card]" in (scrub_text("card 4111 1111 1111 1111") or "")
    # One digit off: fails Luhn, so it is left alone rather than mangling data
    # that merely looks card-shaped.
    assert "[card]" not in (scrub_text("card 4111 1111 1111 1112") or "")


def test_a_tfn_needs_the_ato_checksum() -> None:
    # 123456782 satisfies the ATO weighting; 123456789 does not.
    assert "[tfn]" in (scrub_text("my tfn is 123456782") or "")
    assert "[tfn]" not in (scrub_text("my tfn is 123456789") or "")


def test_empty_and_none_pass_through() -> None:
    assert scrub_text(None) is None
    assert scrub_text("") == ""
    assert not found_pii(None)


def test_scrub_is_idempotent() -> None:
    # The audit row and the message row are scrubbed independently, so scrubbing
    # already-scrubbed text must not keep changing it.
    once = scrub_text("reach me on 0412 345 678 or a@b.co")
    assert scrub_text(once) == once
