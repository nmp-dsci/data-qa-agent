"""Google Sheets / Slides / Drive client — the agent's presentation surface (s46).

This is the **first outbound third-party API call this service makes**. Everything
else it talks to is Postgres, the local sandbox, or the LLM provider, and
``sandbox/runner.py`` blocks sockets outright inside run_analysis. So this client
deliberately lives here in the service process as a governed tool dependency,
never as sandbox code, and it holds the only Google credential the agent has.

Scope discipline (s46 M2):
  * ``drive.file``    — per-file, app-created files only. NOT ``drive``: that is a
    "restricted" scope. ``permissions.create`` accepts ``drive.file``, so sharing
    an artifact we created needs nothing broader.
  * ``spreadsheets``  — create the Sheet, write ranges, add native charts.
  * ``presentations`` — copy the pack, create slides, embed the Sheets charts.

Auth is one long-lived refresh token belonging to a single generating account,
supplied by env like any other secret. There is **no interactive flow and no
fallback**: if the credential is absent the tools are simply not registered, so a
deployment without it (prod/demo) cannot reach Google at all.

Transport is httpx rather than google-api-python-client: the service is async
throughout, the surface used here is a handful of REST calls, and this avoids
pulling a large sync dependency tree into the image.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import settings

TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 — public endpoint, not a secret
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
SLIDES_API = "https://slides.googleapis.com/v1/presentations"
DRIVE_API = "https://www.googleapis.com/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"

# Slides works in EMU (English Metric Units): 914400 per inch. A default Google
# Slides page is 10in x 5.625in (16:9).
EMU_PER_INCH = 914400
PAGE_W = int(10 * EMU_PER_INCH)
PAGE_H = int(5.625 * EMU_PER_INCH)

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# Refresh a little before the token actually dies so a long deck build can't have
# it expire mid-batch.
_TOKEN_SKEW_S = 120


class GoogleAuthUnavailable(RuntimeError):
    """No generating-account credential configured — the deck tools stay unregistered."""


class GoogleApiError(RuntimeError):
    """A Google API call failed. Carries the response body, which is where the
    actual cause lives (a bad range, an unknown layout, a blocked share)."""


def credentials_present() -> bool:
    """True when a full refresh-token credential is configured.

    Checked before the deck tools are registered rather than at call time, so a
    run without credentials never offers the model a tool it cannot use.
    """
    return bool(
        settings.google_deck_client_id
        and settings.google_deck_client_secret
        and settings.google_deck_refresh_token
    )


@dataclass
class _Token:
    value: str = ""
    expires_at: float = 0.0

    def valid(self) -> bool:
        return bool(self.value) and time.time() < self.expires_at - _TOKEN_SKEW_S


@dataclass
class GoogleClient:
    """Thin async wrapper over the three REST surfaces the deck build needs."""

    _token: _Token = field(default_factory=_Token)

    # -- auth ---------------------------------------------------------------

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        if self._token.valid():
            return self._token.value
        if not credentials_present():
            raise GoogleAuthUnavailable(
                "Google deck export needs GOOGLE_DECK_CLIENT_ID, GOOGLE_DECK_CLIENT_SECRET "
                "and GOOGLE_DECK_REFRESH_TOKEN. Run scripts/google_auth.py to mint one."
            )
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": settings.google_deck_client_id,
                "client_secret": settings.google_deck_client_secret,
                "refresh_token": settings.google_deck_refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code != 200:
            raise GoogleApiError(f"token refresh failed ({resp.status_code}): {resp.text[:400]}")
        body = resp.json()
        self._token = _Token(
            value=str(body["access_token"]),
            expires_at=time.time() + float(body.get("expires_in", 3600)),
        )
        return self._token.value

    async def _call(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            token = await self._access_token(client)
            resp = await client.request(
                method,
                url,
                json=json,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code >= 400:
            raise GoogleApiError(f"{method} {url} -> {resp.status_code}: {resp.text[:600]}")
        return dict(resp.json()) if resp.content else {}

    # -- drive --------------------------------------------------------------

    async def copy_file(
        self,
        file_id: str,
        name: str,
        *,
        parent: str = "",
        app_properties: dict[str, str] | None = None,
    ) -> str:
        body: dict[str, Any] = {"name": name}
        if parent:
            body["parents"] = [parent]
        if app_properties:
            body["appProperties"] = app_properties
        out = await self._call("POST", f"{DRIVE_API}/{file_id}/copy", json=body)
        return str(out["id"])

    async def create_folder(
        self,
        name: str,
        *,
        parent: str = "",
        app_properties: dict[str, str] | None = None,
    ) -> str:
        body: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME}
        if parent:
            body["parents"] = [parent]
        if app_properties:
            body["appProperties"] = app_properties
        out = await self._call("POST", DRIVE_API, json=body)
        return str(out["id"])

    async def find_files(
        self,
        *,
        app_properties: dict[str, str] | None = None,
        name: str = "",
        mime_type: str = "",
        parent: str = "",
        fields: str = "files(id,name,mimeType,appProperties)",
    ) -> list[dict[str, Any]]:
        """``files.list`` over the app's own files.

        The whole idempotency story of the pack scaffold rests on this: a pack is
        found by its ``appProperties``, never by remembering an id, so re-running
        the scaffold reuses the folder and both files instead of littering Drive
        with v2, v3, v4 copies of the same pack.

        ``drive.file`` scope means this only ever sees files this app created,
        which is also why searching by appProperties is safe — no other app's
        metadata is visible to it.
        """
        clauses = ["trashed = false"]
        for key, value in (app_properties or {}).items():
            clauses.append(f"appProperties has {{ key='{key}' and value='{value}' }}")
        if name:
            clauses.append(f"name = '{name}'")
        if mime_type:
            clauses.append(f"mimeType = '{mime_type}'")
        if parent:
            clauses.append(f"'{parent}' in parents")
        out = await self._call(
            "GET", DRIVE_API, params={"q": " and ".join(clauses), "fields": fields}
        )
        return [dict(f) for f in out.get("files") or []]

    async def update_file(
        self,
        file_id: str,
        *,
        app_properties: dict[str, str] | None = None,
        name: str = "",
        add_parents: str = "",
        remove_parents: str = "",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if app_properties:
            body["appProperties"] = app_properties
        if name:
            body["name"] = name
        params: dict[str, Any] = {}
        if add_parents:
            params["addParents"] = add_parents
        if remove_parents:
            params["removeParents"] = remove_parents
        return await self._call("PATCH", f"{DRIVE_API}/{file_id}", json=body, params=params)

    async def file_meta(
        self, file_id: str, *, fields: str = "id,name,version,modifiedTime"
    ) -> dict[str, Any]:
        """Drive metadata. ``version`` is the cheap change detector the handover
        poller keys on: it increments on every content edit, so one small GET
        answers "has a human touched this deck?" without reading the deck."""
        return await self._call("GET", f"{DRIVE_API}/{file_id}", params={"fields": fields})

    async def share_public(self, file_id: str) -> None:
        """Anyone with the link may view.

        ``allowFileDiscovery`` is left at its default (false) on purpose: that is
        "anyone with the link", not "published to the web and indexable".

        This is the call that steps outside RLS, so the caller — not this client —
        is responsible for only reaching it when public sharing is intended.
        """
        await self._call(
            "POST",
            f"{DRIVE_API}/{file_id}/permissions",
            json={"type": "anyone", "role": "reader"},
        )

    # -- sheets -------------------------------------------------------------

    async def create_spreadsheet(self, title: str) -> tuple[str, int]:
        """Create a spreadsheet; return (spreadsheetId, first sheetId)."""
        out = await self._call("POST", SHEETS_API, json={"properties": {"title": title}})
        sheet_id = int(out["sheets"][0]["properties"]["sheetId"])
        return str(out["spreadsheetId"]), sheet_id

    async def add_sheet(self, spreadsheet_id: str, title: str) -> int:
        out = await self.sheets_batch(
            spreadsheet_id, [{"addSheet": {"properties": {"title": title}}}]
        )
        return int(out["replies"][0]["addSheet"]["properties"]["sheetId"])

    async def sheets_batch(
        self, spreadsheet_id: str, requests: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return await self._call(
            "POST", f"{SHEETS_API}/{spreadsheet_id}:batchUpdate", json={"requests": requests}
        )

    async def get_spreadsheet(
        self, spreadsheet_id: str, *, fields: str = "", include_grid_data: bool = False
    ) -> dict[str, Any]:
        """``spreadsheets.get``. ``fields`` matters: asking for
        ``sheets(properties,charts)`` returns every chart's full ChartSpec, which
        is what the builder clones — and skips the grid data, which would be
        megabytes."""
        params: dict[str, Any] = {}
        if fields:
            params["fields"] = fields
        if include_grid_data:
            params["includeGridData"] = "true"
        return await self._call("GET", f"{SHEETS_API}/{spreadsheet_id}", params=params)

    async def write_values(
        self,
        spreadsheet_id: str,
        a1_range: str,
        values: list[list[Any]],
        *,
        raw: bool = True,
    ) -> None:
        """Write a range. ``raw=False`` (USER_ENTERED) is what makes a written
        ``=HYPERLINK(...)`` a link rather than the literal text of a formula."""
        await self._call(
            "PUT",
            f"{SHEETS_API}/{spreadsheet_id}/values/{a1_range}",
            params={"valueInputOption": "RAW" if raw else "USER_ENTERED"},
            json={"values": values},
        )

    async def read_values(self, spreadsheet_id: str, a1_range: str) -> list[list[Any]]:
        """Read a range back. Used by the eval graders, which assert on what the
        user actually receives rather than on an in-process object."""
        out = await self._call("GET", f"{SHEETS_API}/{spreadsheet_id}/values/{a1_range}")
        rows = out.get("values") or []
        return [list(r) for r in rows]

    # -- slides -------------------------------------------------------------

    async def create_presentation(self, title: str) -> str:
        out = await self._call("POST", SLIDES_API, json={"title": title})
        return str(out["presentationId"])

    async def get_presentation_pages(self, presentation_id: str, *, fields: str) -> dict[str, Any]:
        """``presentations.get`` with a field mask — the snapshot path reads only
        the handful of properties it diffs, not the whole document."""
        return await self._call("GET", f"{SLIDES_API}/{presentation_id}", params={"fields": fields})

    async def get_presentation(self, presentation_id: str) -> dict[str, Any]:
        return await self._call("GET", f"{SLIDES_API}/{presentation_id}")

    async def slides_batch(
        self, presentation_id: str, requests: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """One ordered, atomic batch. If any sub-request is invalid none apply,
        which is why a slide either lands whole or not at all."""
        return await self._call(
            "POST", f"{SLIDES_API}/{presentation_id}:batchUpdate", json={"requests": requests}
        )

    async def page_thumbnail(
        self, presentation_id: str, page_object_id: str, *, size: str = "MEDIUM"
    ) -> str:
        """``presentations.pages.getThumbnail`` for one library slide (Pack
        Inspector, s48 §P2). This is the expensive read of that feature — one
        HTTP round trip per slide — so callers must cache the result, keyed on
        (slides_id, Drive ``version``), rather than call this on every request."""
        out = await self._call(
            "GET",
            f"{SLIDES_API}/{presentation_id}/pages/{page_object_id}/thumbnail",
            params={"thumbnailProperties.thumbnailSize": size},
        )
        return str(out.get("contentUrl") or "")


def sheet_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def deck_url(presentation_id: str) -> str:
    return f"https://docs.google.com/presentation/d/{presentation_id}/edit"


def deck_embed_url(presentation_id: str) -> str:
    """The in-app viewer URL (s46 D3).

    ``/embed`` renders the deck without Slides' editor chrome, which is what the
    answer area shows; the ``/edit`` URL above is the link out for someone who
    wants to change it.
    """
    return f"https://docs.google.com/presentation/d/{presentation_id}/embed"
