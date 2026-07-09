from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from ..agent_client import fetch_agent_config
from ..auth import CurrentUser, require_admin
from ..config import settings
from ..db import jsonable, rls_connection

router = APIRouter(tags=["admin"])
log = logging.getLogger(__name__)


class AgentConfigEntry(BaseModel):
    """One published composition building block (template or chart)."""

    kind: str  # template | chart
    name: str
    title: str
    description: str
    spec: dict[str, Any]
    demo: dict[str, Any]


class AgentConfigResponse(BaseModel):
    templates: list[AgentConfigEntry]
    charts: list[AgentConfigEntry]


@router.get("/admin/agent-config", response_model=AgentConfigResponse)
async def admin_agent_config(
    admin: CurrentUser = Depends(require_admin),
) -> AgentConfigResponse:
    """The published composition registry: page layouts + charts the agent can use.

    Backed by app.agent_config (migration 0014), demo-seeded from the Hornsby
    worked example so admins can see what the agent composes with at a glance.
    """
    async with rls_connection(admin.id) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT kind, name, title, description, spec, demo "
                        "FROM app.agent_config ORDER BY kind, sort, name"
                    )
                )
            )
            .mappings()
            .all()
        )
    entries = [
        AgentConfigEntry.model_validate({k: jsonable(v) for k, v in r.items()}) for r in rows
    ]
    return AgentConfigResponse(
        templates=[e for e in entries if e.kind == "template"],
        charts=[e for e in entries if e.kind == "chart"],
    )


class ConfigItem(BaseModel):
    key: str  # the env var / setting name
    value: str  # display value (secrets shown as "set"/"not set", never the value)
    note: str | None = None  # short human hint (allowed values, what the setting does)
    secret: bool = False


class ConfigSection(BaseModel):
    title: str
    service: str
    items: list[ConfigItem]
    error: str | None = None  # set when a section could not be loaded (e.g. agent down)


class ConfigResponse(BaseModel):
    sections: list[ConfigSection]


def _redact_db_url(url: str) -> str:
    """Strip credentials from a SQLAlchemy URL: keep driver/host/db, hide user:pw."""
    try:
        scheme, rest = url.split("://", 1)
    except ValueError:
        return "***"
    if "@" in rest:
        rest = rest.split("@", 1)[1]
    return f"{scheme}://***@{rest}"


def _secret_item(key: str, value: str | None, note: str | None = None) -> ConfigItem:
    return ConfigItem(key=key, value="set" if value else "not set", note=note, secret=True)


def _backend_section() -> ConfigSection:
    s = settings
    items = [
        ConfigItem(key="APP_ENV", value=s.app_env),
        ConfigItem(key="AUTH_MODE", value=s.auth_mode, note="dev = local stub | google = OIDC"),
        ConfigItem(key="AGENT_URL", value=s.agent_url, note="data-agent service base URL"),
        ConfigItem(
            key="DATABASE_URL", value=_redact_db_url(s.database_url), note="app role; RLS enforced"
        ),
        ConfigItem(key="DB_SSL", value=s.db_ssl or "(none)"),
        _secret_item("JWT_SECRET", s.jwt_secret, note="dev-auth signing key"),
        ConfigItem(key="JWT_ALG", value=s.jwt_alg),
        ConfigItem(key="JWT_TTL_SECONDS", value=str(s.jwt_ttl_seconds), note="dev token lifetime"),
        ConfigItem(key="GOOGLE_CLIENT_ID", value=s.google_client_id or "(none)"),
        ConfigItem(key="ADMIN_EMAILS", value=s.admin_emails or "(none)", note="→ admin role"),
        ConfigItem(key="CORS_ORIGINS", value=", ".join(s.all_cors_origins)),
    ]
    return ConfigSection(title="Backend API", service="backend-api", items=items)


@router.get("/admin/config", response_model=ConfigResponse)
async def admin_config(admin: CurrentUser = Depends(require_admin)) -> ConfigResponse:
    """Resolved runtime config across services for the admin panel. Secrets are redacted."""
    sections = [_backend_section()]
    try:
        agent = await fetch_agent_config()
        sections.append(ConfigSection.model_validate(agent))
    except Exception as exc:  # data-agent unreachable — degrade, don't 500
        log.warning("failed to fetch data-agent config: %s", exc)
        sections.append(
            ConfigSection(
                title="Data agent",
                service="data-agent",
                items=[],
                error=f"could not reach data-agent ({settings.agent_url})",
            )
        )
    return ConfigResponse(sections=sections)
