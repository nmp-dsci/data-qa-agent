from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..agent_client import fetch_agent_config
from ..auth import CurrentUser, require_admin
from ..config import settings

router = APIRouter(tags=["admin"])
log = logging.getLogger(__name__)


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
        ConfigItem(key="AUTH_MODE", value=s.auth_mode, note="dev = local stub | entra = OIDC"),
        ConfigItem(key="AGENT_URL", value=s.agent_url, note="data-agent service base URL"),
        ConfigItem(
            key="DATABASE_URL", value=_redact_db_url(s.database_url), note="app role; RLS enforced"
        ),
        ConfigItem(key="DB_SSL", value=s.db_ssl or "(none)"),
        _secret_item("JWT_SECRET", s.jwt_secret, note="dev-auth signing key"),
        ConfigItem(key="JWT_ALG", value=s.jwt_alg),
        ConfigItem(key="JWT_TTL_SECONDS", value=str(s.jwt_ttl_seconds), note="dev token lifetime"),
        ConfigItem(key="ENTRA_AUTHORITY", value=s.entra_authority or "(none)"),
        ConfigItem(key="ENTRA_CLIENT_ID", value=s.entra_client_id or "(none)"),
        ConfigItem(key="ENTRA_AUDIENCE", value=s.expected_audience or "(none)"),
        ConfigItem(key="ENTRA_ADMIN_ROLE", value=s.entra_admin_role),
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
