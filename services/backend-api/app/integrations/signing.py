"""Request/response signing for the non-UI surfaces (s35 rungs 1-2).

Two different jobs that are easy to conflate:

* **Slack inbound** — Slack will never send our ``dpk_`` key, so its signature is
  how we know the request is genuine. It proves the REQUEST, not the identity;
  what the caller may see is still decided by the service account behind it.
* **Webhook outbound** — we sign what we POST back, so the receiver can tell our
  callback from anything else that learns its URL.

Both verify over the RAW body. Verifying a re-serialised dict is the classic way
to get a signature check that passes locally and fails intermittently in
production, because key order and separator whitespace are not stable.
"""

from __future__ import annotations

import hashlib
import hmac
import time

# Slack's documented window. Older than this and we refuse, so a captured
# request can't be replayed later.
REPLAY_WINDOW_S = 60 * 5


def sign_payload(secret: str, body: bytes, timestamp: str) -> str:
    """Our own outbound signature: hex HMAC-SHA256 over ``{timestamp}.{body}``."""
    base = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def verify_slack(secret: str, body: bytes, timestamp: str, signature: str, *, now: float) -> bool:
    """Verify Slack's ``X-Slack-Signature`` (``v0=<hex>`` over ``v0:ts:body``).

    ``now`` is injected rather than read from the clock so the replay window is
    testable without freezing time.
    """
    if not secret or not timestamp or not signature:
        return False
    try:
        age = abs(now - float(timestamp))
    except ValueError:
        return False
    if age > REPLAY_WINDOW_S:
        return False
    base = f"v0:{timestamp}:".encode() + body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def now_timestamp() -> str:
    return str(int(time.time()))
