"""Admin CRUD for service-account keys (s35 rung 4).

Admin-only, mirroring every other /admin route in this service. The one thing
this API does that others don't: the full key is returned exactly once, by
create, and can never be read back. Only the key_id (the public half) appears in
listings, so a leaked database dump or an over-shared screenshot doesn't hand
anyone a working credential.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..auth import CurrentUser, require_admin
from ..db import rls_connection
from ..service_auth import SURFACES, mint_key

router = APIRouter(tags=["service-accounts"])


class ServiceAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    surface: str
    # Which datasets the bot may read. This IS the security policy for the
    # surface — with channel membership as the access boundary (s35), an
    # over-broad grant here is the way this design actually goes wrong.
    dataset_slugs: list[str] = Field(default_factory=list)


@router.get("/admin/service-accounts")
async def list_service_accounts(
    admin: CurrentUser = Depends(require_admin),
) -> list[dict[str, Any]]:
    async with rls_connection(admin.id) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT sa.id, sa.name, sa.surface, sa.key_id, sa.created_at, "
                        "       sa.last_used_at, sa.revoked_at, u.username "
                        "FROM app.service_accounts sa JOIN app.users u ON u.id = sa.user_id "
                        "ORDER BY sa.created_at DESC"
                    )
                )
            )
            .mappings()
            .all()
        )
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "surface": r["surface"],
            # The public half only. The secret is unrecoverable by design.
            "key_id": r["key_id"],
            "username": r["username"],
            "created_at": r["created_at"].isoformat(),
            "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
            "revoked_at": r["revoked_at"].isoformat() if r["revoked_at"] else None,
        }
        for r in rows
    ]


@router.post("/admin/service-accounts", status_code=201)
async def create_service_account(
    body: ServiceAccountCreate, admin: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    """Mint a key. The response is the ONLY time the full key exists."""
    if body.surface not in SURFACES:
        raise HTTPException(status_code=400, detail=f"surface must be one of {', '.join(SURFACES)}")
    full_key, key_id, key_hash = mint_key()
    # Deterministic from the surface + name so re-creating after a revoke reuses
    # the same identity, and so the username is legible in the audit trail.
    slug = "".join(c if c.isalnum() else "-" for c in body.name.lower())[:40].strip("-")
    external_id = f"{body.surface}:{slug}"
    username = f"svc-{body.surface}-{slug}"[:60]

    async with rls_connection(admin.id) as conn:
        user_row = (
            (
                await conn.execute(
                    text(
                        "INSERT INTO app.users "
                        "  (auth_provider, external_id, username, email, display_name, role, plan) "
                        "VALUES ('service', :ext, :username, :email, :name, 'user', 'service') "
                        "ON CONFLICT (auth_provider, external_id) DO UPDATE SET "
                        "  display_name = EXCLUDED.display_name "
                        "RETURNING id"
                    ),
                    {
                        "ext": external_id,
                        "username": username,
                        "email": f"{username}@service.local",
                        "name": body.name,
                    },
                )
            )
            .mappings()
            .first()
        )
        assert user_row is not None  # noqa: S101 - RETURNING always yields a row
        user_id = str(user_row["id"])

        account = (
            (
                await conn.execute(
                    text(
                        "INSERT INTO app.service_accounts "
                        "  (user_id, name, key_id, key_hash, surface) "
                        "VALUES (CAST(:uid AS uuid), :name, :key_id, :key_hash, :surface) "
                        "RETURNING id, created_at"
                    ),
                    {
                        "uid": user_id,
                        "name": body.name,
                        "key_id": key_id,
                        "key_hash": key_hash,
                        "surface": body.surface,
                    },
                )
            )
            .mappings()
            .first()
        )
        assert account is not None  # noqa: S101 - RETURNING always yields a row

        for slug_name in body.dataset_slugs:
            await conn.execute(
                text(
                    "INSERT INTO app.dataset_access (dataset_id, user_id, access) "
                    "SELECT d.id, CAST(:uid AS uuid), 'read' FROM app.datasets d "
                    "WHERE d.slug = :slug ON CONFLICT DO NOTHING"
                ),
                {"uid": user_id, "slug": slug_name},
            )

    return {
        "id": str(account["id"]),
        "name": body.name,
        "surface": body.surface,
        "key_id": key_id,
        "username": username,
        # Shown once. Never retrievable again — the UI must make that unmistakable.
        "key": full_key,
    }


@router.post("/admin/service-accounts/{account_id}/revoke")
async def revoke_service_account(
    account_id: str, admin: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    async with rls_connection(admin.id) as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "UPDATE app.service_accounts SET revoked_at = now() "
                        "WHERE id = CAST(:id AS uuid) AND revoked_at IS NULL "
                        "RETURNING id, revoked_at"
                    ),
                    {"id": account_id},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise HTTPException(status_code=404, detail="No such active service account")
    return {"id": str(row["id"]), "revoked_at": row["revoked_at"].isoformat()}
