"""Slack signature verification and callback signing (s35 rungs 1-2).

The Slack half is a security boundary: it is the *only* thing standing between
an arbitrary internet caller and a question that spends LLM budget under the
bot's data grants. So the negative cases matter more than the happy path.
"""

from __future__ import annotations

import hashlib
import hmac

from app.integrations.signing import REPLAY_WINDOW_S, sign_payload, verify_slack

SECRET = "test-signing-secret"  # noqa: S105 - a fixture, not a credential
NOW = 1_800_000_000.0


def _slack_sig(body: bytes, timestamp: str, secret: str = SECRET) -> str:
    base = f"v0:{timestamp}:".encode() + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def test_valid_signature_passes() -> None:
    body, ts = b"text=median+rent&user_id=U1", str(int(NOW))
    assert verify_slack(SECRET, body, ts, _slack_sig(body, ts), now=NOW)


def test_tampered_body_fails() -> None:
    ts = str(int(NOW))
    signature = _slack_sig(b"text=median+rent", ts)
    # Same signature, different body — the exact shape of a modified replay.
    assert not verify_slack(SECRET, b"text=drop+everything", ts, signature, now=NOW)


def test_wrong_secret_fails() -> None:
    body, ts = b"text=hello", str(int(NOW))
    assert not verify_slack(SECRET, body, ts, _slack_sig(body, ts, "other-secret"), now=NOW)


def test_stale_timestamp_is_refused() -> None:
    # A captured request replayed later must not work, even though its signature
    # is perfectly valid — this is the whole point of the timestamp.
    old = NOW - REPLAY_WINDOW_S - 1
    body, ts = b"text=hello", str(int(old))
    assert not verify_slack(SECRET, body, ts, _slack_sig(body, ts), now=NOW)


def test_future_timestamp_is_refused() -> None:
    # Clock skew cuts both ways; the window is absolute, not one-sided.
    ahead = NOW + REPLAY_WINDOW_S + 1
    body, ts = b"text=hello", str(int(ahead))
    assert not verify_slack(SECRET, body, ts, _slack_sig(body, ts), now=NOW)


def test_within_window_still_passes() -> None:
    recent = NOW - (REPLAY_WINDOW_S - 5)
    body, ts = b"text=hello", str(int(recent))
    assert verify_slack(SECRET, body, ts, _slack_sig(body, ts), now=NOW)


def test_missing_or_junk_inputs_fail_closed() -> None:
    body, ts = b"text=hello", str(int(NOW))
    good = _slack_sig(body, ts)
    assert not verify_slack("", body, ts, good, now=NOW)  # unconfigured secret
    assert not verify_slack(SECRET, body, "", good, now=NOW)  # no timestamp
    assert not verify_slack(SECRET, body, ts, "", now=NOW)  # no signature
    assert not verify_slack(SECRET, body, "not-a-number", good, now=NOW)
    assert not verify_slack(SECRET, body, ts, "garbage", now=NOW)


def test_outbound_signature_is_stable_and_body_bound() -> None:
    body, ts = b'{"answer":"42"}', "1800000000"
    first = sign_payload(SECRET, body, ts)
    assert first == sign_payload(SECRET, body, ts)
    assert first != sign_payload(SECRET, b'{"answer":"43"}', ts)
    assert first != sign_payload(SECRET, body, "1800000001")
