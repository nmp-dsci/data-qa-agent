from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, cast

import httpx
import jwt
from fastapi import Depends, Header, HTTPException, status
from jwt import PyJWKClient
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from .config import settings
from .db import rls_connection


@dataclass
class CurrentUser:
    id: str
    username: str
    email: str
    role: str
    entra_oid: str | None = None

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
# Entra External ID (auth_mode=entra): validate RS256 tokens against the
# tenant's published JWKS. No client secret is needed to verify a token.
# ---------------------------------------------------------------------------
class EntraVerifier:
    """Lazily loads OIDC metadata + signing keys and verifies access tokens."""

    def __init__(self) -> None:
        self._issuer: str | None = None
        self._jwks_client: PyJWKClient | None = None

    async def _ensure_loaded(self) -> None:
        if self._jwks_client is not None:
            return
        if not settings.entra_authority or not settings.expected_audience:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Entra auth is not configured (entra_authority / entra_client_id)",
            )
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(settings.openid_config_url)
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
            audience=settings.expected_audience,
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


_entra_verifier = EntraVerifier()


async def _provision_entra_user(claims: dict[str, Any]) -> CurrentUser:
    """Just-in-time upsert an Entra identity into app.users, keyed by oid.

    Returns the app-local row so RLS (which compares app.users.id) and the
    admin role stay driven by our own database, refreshed from the token.
    """
    oid = claims.get("oid") or claims.get("sub")
    if not oid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing subject (oid/sub)"
        )
    email = claims.get("email") or claims.get("preferred_username") or f"{oid}@entra.local"
    display_name = claims.get("name") or email
    username = claims.get("preferred_username") or email
    roles = claims.get("roles") or []
    role = "admin" if settings.entra_admin_role in roles else "user"

    async with rls_connection(None) as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "INSERT INTO app.users (entra_oid, username, email, display_name, role) "
                        "VALUES (:oid, :username, :email, :name, :role) "
                        "ON CONFLICT (entra_oid) DO UPDATE SET "
                        "  email = EXCLUDED.email, "
                        "  display_name = EXCLUDED.display_name, "
                        "  role = EXCLUDED.role "
                        "RETURNING id, username, email, role"
                    ),
                    {
                        "oid": oid,
                        "username": username,
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
        entra_oid=oid,
    )


# ---------------------------------------------------------------------------
# FastAPI dependencies (dispatch on auth_mode)
# ---------------------------------------------------------------------------
def _bearer_token(authorization: str | None) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization.split(" ", 1)[1]


async def _user_from_authorization(authorization: str | None) -> CurrentUser | None:
    token = _bearer_token(authorization)
    if token is None:
        return None
    if settings.auth_mode == "entra":
        claims = await _entra_verifier.verify(token)
        return await _provision_entra_user(claims)
    return _dev_user(token)


async def get_current_user(authorization: str | None = Header(default=None)) -> CurrentUser:
    user = await _user_from_authorization(authorization)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


async def get_optional_user(
    authorization: str | None = Header(default=None),
) -> CurrentUser | None:
    return await _user_from_authorization(authorization)


async def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user
