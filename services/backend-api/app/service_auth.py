"""Service-account API keys — minting, parsing and constant-time verification.

A key looks like ``dpk_<key_id>_<secret>``. The key_id half is public (indexed,
safe to log, shown in the admin list); the secret half is 256 bits of CSPRNG
output and is never stored — only its SHA-256. The full key exists exactly once,
in the create response. There is no recovery path, which is the intended design:
losing a key means rotating it.

Why SHA-256 and not bcrypt/argon2 — this reads like a shortcut and isn't:
password KDFs are deliberately slow to make *guessing* expensive, which only
matters when the secret is drawn from a small, human-shaped space. These secrets
are 256 random bits; there is no dictionary to walk and no meaningful offline
attack to slow down, so a slow KDF would buy nothing and cost latency on every
authenticated request. This is the same reasoning behind GitHub's and Stripe's
API tokens. Password-hashing rules do not transfer to high-entropy tokens.
"""

from __future__ import annotations

import hashlib
import secrets

# Namespaced so a key is recognisable in a log or a paste, and so the auth
# dispatcher can tell a service key from a Google ID token without a DB lookup.
PREFIX = "dpk_"

# Surfaces a key may be pinned to. A key is valid on exactly one, so a leaked
# Slack key cannot drive the MCP server or the webhook.
SURFACES = ("webhook", "slack", "mcp")


def mint_key() -> tuple[str, str, str]:
    """Return ``(full_key, key_id, key_hash)``. Show ``full_key`` once, then drop it."""
    key_id = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    return f"{PREFIX}{key_id}_{secret}", key_id, _hash(secret)


def split_key(token: str) -> tuple[str, str] | None:
    """``dpk_<key_id>_<secret>`` -> ``(key_id, secret)``, or None if not a service key.

    Returning None is how the auth dispatcher decides this isn't a service key
    at all and should fall through to Google/dev — so this must never raise on
    arbitrary input, including a Google ID token that happens to be malformed.
    """
    if not token.startswith(PREFIX):
        return None
    key_id, _, secret = token[len(PREFIX) :].partition("_")
    if not key_id or not secret:
        return None
    return key_id, secret


def verify(secret: str, key_hash: str) -> bool:
    """Constant-time comparison of a presented secret against the stored hash."""
    return secrets.compare_digest(_hash(secret), key_hash)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()
