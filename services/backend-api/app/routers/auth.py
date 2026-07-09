from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from ..auth import CurrentUser, create_access_token, get_current_user
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
    if settings.auth_mode == "google":
        return AuthConfig(
            auth_mode="google",
            client_id=settings.google_client_id,
            scopes=["openid", "email", "profile"],
        )
    return AuthConfig(auth_mode="dev")


@router.post("/auth/dev-login", response_model=TokenResponse)
async def dev_login(body: DevLoginRequest) -> TokenResponse:
    """Local dev-auth stub. In production this is replaced by Google OIDC."""
    if settings.auth_mode != "dev":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Dev login disabled")

    async with rls_connection(None) as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT id, username, email, display_name, role "
                        "FROM app.users WHERE username = :u"
                    ),
                    {"u": body.username},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail=f"Unknown user '{body.username}'")

        await conn.execute(
            text(
                "INSERT INTO app.events (user_id, event_type, payload) "
                "VALUES (:uid, 'login_success', '{}'::jsonb)"
            ),
            {"uid": str(row["id"])},
        )

    token = create_access_token(
        user_id=str(row["id"]),
        username=row["username"],
        email=row["email"],
        role=row["role"],
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
