from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from ..auth import SESSION_COOKIE_NAME, CurrentUser, create_access_token, get_current_user
from ..config import settings
from ..db import rls_connection

router = APIRouter(tags=["auth"])


class DevLoginRequest(BaseModel):
    username: str


class UserOut(BaseModel):
    id: str
    username: str
    email: str
    display_name: str
    role: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class AuthConfig(BaseModel):
    auth_mode: str
    authority: str | None = None
    client_id: str | None = None
    scopes: list[str] = []


@router.get("/auth/config", response_model=AuthConfig)
async def auth_config() -> AuthConfig:
    """Lets the frontend configure its login flow at runtime (no rebuild to flip).

    Dev mode returns just the mode; Google mode returns the OAuth client id the
    Google Identity Services button needs.
    """
    if settings.demo_mode:
        # s38: the walk-in demo door. client_id rides along when Google is
        # configured so the owner door (/login route, admins only) can render
        # the real sign-in button on the same deployment.
        return AuthConfig(
            auth_mode="demo",
            client_id=settings.google_client_id or None,
            scopes=["openid", "email", "profile"] if settings.google_client_id else [],
        )
    if settings.auth_mode == "google":
        return AuthConfig(
            auth_mode="google",
            client_id=settings.google_client_id,
            scopes=["openid", "email", "profile"],
        )
    return AuthConfig(auth_mode="dev")


async def _session_for(username: str, response: Response, event: str) -> TokenResponse:
    """Mint the local HS256 session for a seeded user row (dev + demo doors)."""
    async with rls_connection(None) as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT id, username, email, display_name, role "
                        "FROM app.users WHERE username = :u"
                    ),
                    {"u": username},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail=f"Unknown user '{username}'")

        await conn.execute(
            text(
                "INSERT INTO app.events (user_id, event_type, payload) "
                "VALUES (:uid, :etype, '{}'::jsonb)"
            ),
            {"uid": str(row["id"]), "etype": event},
        )

    token = create_access_token(
        user_id=str(row["id"]),
        username=row["username"],
        email=row["email"],
        role=row["role"],
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.jwt_ttl_seconds,
        httponly=True,
        samesite="lax",
        # Secure requires TLS; local dev serves over plain http://localhost.
        secure=settings.app_env != "dev",
        path="/",
    )
    return TokenResponse(
        access_token=token,
        user=UserOut(
            id=str(row["id"]),
            username=row["username"],
            email=row["email"],
            display_name=row["display_name"],
            role=row["role"],
        ),
    )


@router.post("/auth/dev-login", response_model=TokenResponse)
async def dev_login(body: DevLoginRequest, response: Response) -> TokenResponse:
    """Local dev-auth stub. In production this is replaced by Google OIDC.

    Sets the session as an httpOnly cookie *in addition to* returning it in the
    body — the body keeps scripts.eval_run/smoke_test/CI journeys working
    exactly as before (they use the bearer, never a cookie jar), while the
    cookie lets a browser reload survive without re-logging in.
    """
    if settings.auth_mode != "dev":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Dev login disabled")
    return await _session_for(body.username, response, "login_success")


@router.post("/auth/demo-login", response_model=TokenResponse)
async def demo_login(response: Response) -> TokenResponse:
    """The walk-in demo door (s38 P0): one click, no account, no body.

    Mints the same 8h HS256 session dev-login does, for the one seeded demo
    user (migration 0033). 404 outside demo mode — mirroring how dev-login
    refuses outside auth_mode=dev — so a live deployment does not carry a
    passwordless login lying dormant.
    """
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Not found")
    return await _session_for(settings.demo_username, response, "demo_login_success")


@router.post("/auth/logout")
async def logout(response: Response) -> dict[str, bool]:
    """Clear the session cookie. httpOnly means the frontend cannot do this
    itself with document.cookie, so sign-out has to be a round trip."""
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser = Depends(get_current_user)) -> UserOut:
    async with rls_connection(user.id) as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT id, username, email, display_name, role "
                        "FROM app.users WHERE id = :id"
                    ),
                    {"id": user.id},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserOut(
        id=str(row["id"]),
        username=row["username"],
        email=row["email"],
        display_name=row["display_name"],
        role=row["role"],
    )
