from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, cast

import httpx
import jwt
from fastapi import Cookie, Depends, Header, HTTPException, status
from jwt import PyJWKClient
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from .config import settings
from .db import rls_connection

# Dev-auth stub only (auth_mode=dev): the local HS256 session token, also set
# as an httpOnly cookie by /auth/dev-login so a page reload doesn't drop the
# session — the frontend previously held the token only in a JS variable,
# which a reload wipes even though the token itself is still valid for
# jwt_ttl_seconds. Google mode is untouched: its ID token is verified as a
# bearer header on every request, same as before. Unifying that onto the same
# cookie would need SameSite=None (frontend and backend sit on different
# registrable domains in prod — CloudFront vs App Runner, see
# infra/terraform/foundations/apprunner.tf) which is a materially different
# and riskier decision than this dev-only convenience fix.
SESSION_COOKIE_NAME = "dp_session"


@dataclass
class CurrentUser:
    id: str
    username: str
    email: str
    role: str
    external_id: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# ---------------------------------------------------------------------------
# Dev-auth stub (auth_mode=dev): a locally signed HS256 token.
# ---------------------------------------------------------------------------
def create_access_token(*, user_id: str, username: str, email: str, role: str) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "username": username,
        "email": email,
        "role": role,
        "iat": now,
        "exp": now + settings.jwt_ttl_seconds,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def _decode_dev(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg])
    except jwt.PyJWTError as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        ) from exc


def _dev_user(token: str) -> CurrentUser:
    claims = _decode_dev(token)
    return CurrentUser(
        id=claims["sub"],
        username=claims.get("username", ""),
        email=claims.get("email", ""),
        role=claims.get("role", "user"),
    )


# ---------------------------------------------------------------------------
# Google Sign-in (auth_mode=google): validate RS256 ID tokens against Google's
# published JWKS. No client secret is needed to verify a token.
# ---------------------------------------------------------------------------
GOOGLE_OPENID_CONFIG_URL = "https://accounts.google.com/.well-known/openid-configuration"


class GoogleVerifier:
    """Lazily loads Google's OIDC metadata + signing keys and verifies ID tokens."""

    def __init__(self) -> None:
        self._issuer: str | None = None
        self._jwks_client: PyJWKClient | None = None

    async def _ensure_loaded(self) -> None:
        if self._jwks_client is not None:
            return
        if not settings.google_client_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Google auth is not configured (google_client_id)",
            )
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(GOOGLE_OPENID_CONFIG_URL)
            resp.raise_for_status()
            meta = resp.json()
        self._issuer = meta["issuer"]
        self._jwks_client = PyJWKClient(meta["jwks_uri"])

    def _verify_sync(self, token: str) -> dict[str, Any]:
        assert self._jwks_client is not None  # noqa: S101 - guarded by _ensure_loaded
        signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.google_client_id,
            issuer=self._issuer,
            options={"require": ["exp", "iss", "aud"]},
        )

    async def verify(self, token: str) -> dict[str, Any]:
        await self._ensure_loaded()
        try:
            # PyJWKClient + jwt.decode do blocking work; keep the loop free.
            return cast(dict[str, Any], await run_in_threadpool(self._verify_sync, token))
        except jwt.PyJWTError as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
            ) from exc


_google_verifier = GoogleVerifier()


async def _provision_google_user(claims: dict[str, Any]) -> CurrentUser:
    """Just-in-time upsert a Google identity into app.users, keyed by sub.

    Returns the app-local row so RLS (which compares app.users.id) and the
    admin role stay driven by our own database, refreshed from the token. The
    admin role comes from the ADMIN_EMAILS allowlist, not from the token.
    """
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing subject (sub)"
        )
    email = claims.get("email")
    if not email or not claims.get("email_verified"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing a verified email"
        )
    display_name = claims.get("name") or email
    role = "admin" if email.lower() in settings.admin_email_set else "user"

    async with rls_connection(None) as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "INSERT INTO app.users "
                        "  (auth_provider, external_id, username, email, display_name, role) "
                        "VALUES ('google', :sub, :username, :email, :name, :role) "
                        "ON CONFLICT (auth_provider, external_id) DO UPDATE SET "
                        "  email = EXCLUDED.email, "
                        "  display_name = EXCLUDED.display_name, "
                        "  role = EXCLUDED.role "
                        "RETURNING id, username, email, role"
                    ),
                    {
                        "sub": sub,
                        "username": email,
                        "email": email,
                        "name": display_name,
                        "role": role,
                    },
                )
            )
            .mappings()
            .first()
        )
    assert row is not None  # noqa: S101 - RETURNING always yields a row
    return CurrentUser(
        id=str(row["id"]),
        username=row["username"],
        email=row["email"],
        role=row["role"],
        external_id=sub,
    )


# ---------------------------------------------------------------------------
# FastAPI dependencies (dispatch on auth_mode)
# ---------------------------------------------------------------------------
def _bearer_token(authorization: str | None) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization.split(" ", 1)[1]


async def _user_from_credentials(
    authorization: str | None, session_cookie: str | None
) -> CurrentUser | None:
    # Header first, so nothing that already sends an explicit bearer (scripts,
    # smoke tests, the CI journeys, Google mode's ID token) changes behaviour.
    # The cookie is only ever populated by /auth/dev-login, which itself
    # refuses to set it outside auth_mode=dev — mirror that gate here so the
    # server never accepts a dev-mode cookie while running in Google mode.
    token = _bearer_token(authorization)
    if token is not None:
        if settings.auth_mode == "google":
            claims = await _google_verifier.verify(token)
            return await _provision_google_user(claims)
        return _dev_user(token)
    if session_cookie is not None and settings.auth_mode == "dev":
        return _dev_user(session_cookie)
    return None


async def get_current_user(
    authorization: str | None = Header(default=None),
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> CurrentUser:
    user = await _user_from_credentials(authorization, session_cookie)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


async def get_optional_user(
    authorization: str | None = Header(default=None),
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> CurrentUser | None:
    return await _user_from_credentials(authorization, session_cookie)


async def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user
