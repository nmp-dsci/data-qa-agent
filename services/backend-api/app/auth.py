from __future__ import annotations

import time
from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException, status

from .config import settings


@dataclass
class CurrentUser:
    id: str
    username: str
    email: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


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


def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg])
    except jwt.PyJWTError as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        ) from exc


def _user_from_authorization(authorization: str | None) -> CurrentUser | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    claims = _decode(authorization.split(" ", 1)[1])
    return CurrentUser(
        id=claims["sub"],
        username=claims.get("username", ""),
        email=claims.get("email", ""),
        role=claims.get("role", "user"),
    )


def get_current_user(authorization: str | None = Header(default=None)) -> CurrentUser:
    user = _user_from_authorization(authorization)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def get_optional_user(authorization: str | None = Header(default=None)) -> CurrentUser | None:
    return _user_from_authorization(authorization)


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user
