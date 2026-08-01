"""Service-account key handling (s35 rung 0).

Pure unit tests against the key primitives — no DB, no HTTP. The DB-backed half
(revocation, surface pinning, last_used_at) is covered by the integration tests
that run against the live stack; what matters here is that the parsing and
comparison primitives can't be tricked, since they gate a production auth path.
"""

from __future__ import annotations

from app.service_auth import PREFIX, mint_key, split_key, verify


def test_mint_round_trips() -> None:
    full, key_id, key_hash = mint_key()
    parsed = split_key(full)
    assert parsed is not None
    assert parsed[0] == key_id
    assert verify(parsed[1], key_hash)


def test_mint_is_unique_per_call() -> None:
    # Two keys minted back to back must share nothing — a seeded or counter-based
    # generator would make one key predictable from another.
    first, first_id, first_hash = mint_key()
    second, second_id, second_hash = mint_key()
    assert first != second
    assert first_id != second_id
    assert first_hash != second_hash


def test_wrong_secret_is_rejected() -> None:
    _, _, key_hash = mint_key()
    other_full, _, _ = mint_key()
    other_secret = split_key(other_full)
    assert other_secret is not None
    assert not verify(other_secret[1], key_hash)


def test_split_rejects_non_service_tokens() -> None:
    # split_key is how the auth dispatcher decides a token ISN'T a service key
    # and should fall through to Google/dev. It must never raise, whatever it's
    # handed — including a real-shaped JWT and assorted malformed input.
    assert split_key("eyJhbGciOiJSUzI1NiIsImtpZCI6IjEyMyJ9.eyJzdWIiOiJ4In0.sig") is None
    assert split_key("") is None
    assert split_key("Bearer dpk_abc_def") is None
    assert split_key("DPK_abc_def") is None  # prefix is case-sensitive


def test_split_rejects_malformed_service_tokens() -> None:
    assert split_key(PREFIX) is None  # prefix only
    assert split_key(f"{PREFIX}abc") is None  # no separator, so no secret
    assert split_key(f"{PREFIX}_secret") is None  # empty key_id
    assert split_key(f"{PREFIX}abc_") is None  # empty secret


def test_secret_may_contain_the_separator() -> None:
    # token_urlsafe can emit '_', so the split must take the FIRST separator
    # only. Splitting on the last (or on every) one would intermittently reject
    # perfectly valid keys — roughly a third of them, at random.
    key_id, secret = "abc123", "aa_bb_cc"
    parsed = split_key(f"{PREFIX}{key_id}_{secret}")
    assert parsed == (key_id, secret)
