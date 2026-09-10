"""Pack Inspector — GET/PUT (s48 §P2).

Builds a minimal FastAPI app wired exactly the way `agent/main.py` wires
`/agent/pack` and `/agent/pack/layouts/{id}` (dependency-injecting a
`GoogleClient`), rather than importing the real app — that app's lifespan
opens real DB engines, which this surface has nothing to do with. A
`FakeClient` stands in for Google throughout, matching the pattern already
used for the deck builder in `test_deck.py`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from agent import pack_api
from agent.gsuite import EMU_PER_INCH
from agent.pack_api import (
    PackApiError,
    PackLayoutNotFound,
    PackLayoutUpdate,
    PackLayoutUpdateOut,
    PackOut,
    PackUnavailable,
    get_google_client,
)

app = FastAPI()


@app.get("/agent/pack", response_model=PackOut)
async def agent_pack(client: Any = Depends(get_google_client)) -> PackOut:
    return await pack_api.get_pack(client)


@app.put("/agent/pack/layouts/{layout_id}", response_model=PackLayoutUpdateOut)
async def agent_pack_layout_update(
    layout_id: str, body: PackLayoutUpdate, client: Any = Depends(get_google_client)
) -> PackLayoutUpdateOut:
    try:
        return await pack_api.update_pack_layout(layout_id, body, client=client)
    except PackLayoutNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PackUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PackApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


client = TestClient(app)


def _write_pack_json(tmp_path: Path, *, name: str = "nsw-property") -> None:
    body = {
        "name": name,
        "version": 1,
        "synced_at": "2020-01-01T00:00:00Z",
        "slides_id": "slides-1",
        "sheet_id": "sheet-1",
        "folder_id": "folder-1",
        "page": {"width_in": 10.0, "height_in": 5.625},
        "layouts": [
            {
                "id": "L1",
                "name": "Cover",
                "enabled": True,
                "use_when": "the first slide",
                "source": "slide",
                "ref": "L1",
                "slide_object_id": "lib_L1",
                "slots": {"headline": {"object_id": "L1_headline", "rect": [0.5, 0.3, 9.0, 0.8]}},
                "table_template": "",
                "chart_template": None,
                "series_max": 0,
                "grader_shape": "",
                "issues": [],
            },
            {
                "id": "L2",
                "name": "Headline + Trend",
                "enabled": False,
                "use_when": "a trend",
                "source": "slide",
                "ref": "L2",
                "slide_object_id": "",
                "slots": {},
                "table_template": "tpl_series",
                "chart_template": {
                    "tab": "tpl_series",
                    "title": "trend",
                    "chart_id": 1,
                    "sheet_id": 2,
                },
                "series_max": 3,
                "grader_shape": "series",
                "issues": ["no library slide with ref 'L2'"],
            },
        ],
        "table_templates": {},
        "issues": [],
    }
    out = tmp_path / name / "pack.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(body, indent=2), encoding="utf-8")


@pytest.fixture(autouse=True)
def _clear_thumb_cache() -> None:
    pack_api._thumb_cache.clear()


# --------------------------------------------------------------------------
# GET — offline (no synced pack / no credentials)
# --------------------------------------------------------------------------


def test_get_pack_with_no_synced_pack_reports_an_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pack_api.settings, "pack_dir", str(tmp_path))
    monkeypatch.setattr(pack_api.settings, "pack_name", "nsw-property")
    resp = client.get("/agent/pack")
    assert resp.status_code == 200
    body = resp.json()
    assert body["layouts"] == []
    assert any("no synced pack" in i for i in body["issues"])


def test_get_pack_without_credentials_returns_pack_json_with_null_thumbnails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_pack_json(tmp_path)
    monkeypatch.setattr(pack_api.settings, "pack_dir", str(tmp_path))
    monkeypatch.setattr(pack_api.settings, "pack_name", "nsw-property")
    monkeypatch.setattr(pack_api, "credentials_present", lambda: False)

    resp = client.get("/agent/pack")
    assert resp.status_code == 200
    body = resp.json()
    assert [layout["id"] for layout in body["layouts"]] == ["L1", "L2"]
    assert all(layout["thumbnail_url"] is None for layout in body["layouts"])
    assert body["stale"] is False
    # A layout with issues is reported (and disabled), never dropped.
    l2 = next(layout for layout in body["layouts"] if layout["id"] == "L2")
    assert l2["enabled"] is False
    assert l2["issues"]
    assert l2["chart_template"] == "tpl_series!trend"
    assert body["slides_url"].endswith("slides-1/edit")
    assert body["sheet_url"].endswith("sheet-1/edit")
    assert body["folder_url"].endswith("folder-1")


# --------------------------------------------------------------------------
# GET — online (thumbnails + staleness)
# --------------------------------------------------------------------------


class _ThumbClient:
    def __init__(self) -> None:
        self.thumbnail_calls = 0

    async def file_meta(self, file_id: str, *, fields: str = "") -> dict[str, Any]:
        if file_id == "slides-1":
            return {"version": "7"}
        return {"modifiedTime": "2099-01-01T00:00:00Z"}  # newer than synced_at -> stale

    async def page_thumbnail(
        self, presentation_id: str, page_object_id: str, *, size: str = "MEDIUM"
    ) -> str:
        self.thumbnail_calls += 1
        return f"https://thumb.example/{page_object_id}.png"


def test_get_pack_with_credentials_fetches_and_caches_thumbnails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_pack_json(tmp_path)
    monkeypatch.setattr(pack_api.settings, "pack_dir", str(tmp_path))
    monkeypatch.setattr(pack_api.settings, "pack_name", "nsw-property")
    monkeypatch.setattr(pack_api, "credentials_present", lambda: True)
    fake = _ThumbClient()
    app.dependency_overrides[get_google_client] = lambda: fake

    try:
        resp = client.get("/agent/pack")
        assert resp.status_code == 200
        body = resp.json()
        l1 = next(layout for layout in body["layouts"] if layout["id"] == "L1")
        assert l1["thumbnail_url"] == "https://thumb.example/lib_L1.png"
        l2 = next(layout for layout in body["layouts"] if layout["id"] == "L2")
        assert l2["thumbnail_url"] is None  # no slide_object_id
        assert body["stale"] is True
        assert fake.thumbnail_calls == 1

        # A second GET must not repeat the expensive thumbnail fetch — it's
        # cached in-process keyed on the Slides file's Drive version.
        client.get("/agent/pack")
        assert fake.thumbnail_calls == 1
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# PUT — writes the changed cell(s), re-syncs, busts the catalogue caches
# --------------------------------------------------------------------------


def _col_index(letters: str) -> int:
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


class FakePutClient:
    """A one-layout `_pack` tab + one library slide, mutable through
    `write_values` exactly like the real Sheet — so a PUT's re-sync reads
    back what the PUT itself just wrote."""

    def __init__(self) -> None:
        self.header = [
            "id",
            "name",
            "enabled",
            "use_when",
            "source",
            "ref",
            "table_template",
            "chart_template",
            "series_max",
            "grader_shape",
            "notes",
        ]
        self.row = [
            "L1",
            "Cover",
            "TRUE",
            "old use_when",
            "slide",
            "L1",
            "",
            "",
            "0",
            "",
            "",
        ]
        self.writes: list[tuple[str, list[list[Any]]]] = []

    async def read_values(self, spreadsheet_id: str, a1_range: str) -> list[list[Any]]:
        return [list(self.header), list(self.row)]

    async def write_values(
        self, spreadsheet_id: str, a1_range: str, values: list[list[Any]], *, raw: bool = True
    ) -> None:
        self.writes.append((a1_range, values))
        m = re.search(r"!([A-Z]+)(\d+)$", a1_range)
        assert m is not None
        self.row[_col_index(m.group(1))] = values[0][0]

    async def get_presentation(self, presentation_id: str) -> dict[str, Any]:
        return {
            "slides": [
                {
                    "objectId": "lib_L1",
                    "pageElements": [
                        {
                            "objectId": "L1_headline",
                            "title": "headline",
                            "size": {
                                "width": {"magnitude": EMU_PER_INCH * 9},
                                "height": {"magnitude": EMU_PER_INCH * 0.8},
                            },
                            "transform": {
                                "translateX": EMU_PER_INCH * 0.5,
                                "translateY": EMU_PER_INCH * 0.3,
                            },
                        }
                    ],
                }
            ],
            "pageSize": {
                "width": {"magnitude": EMU_PER_INCH * 10},
                "height": {"magnitude": EMU_PER_INCH * 5.625},
            },
        }

    async def get_spreadsheet(self, spreadsheet_id: str, **kwargs: Any) -> dict[str, Any]:
        return {"sheets": [{"properties": {"title": "_pack", "sheetId": 1}, "charts": []}]}

    async def find_files(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def file_meta(self, file_id: str, *, fields: str = "") -> dict[str, Any]:
        return {"version": "1", "modifiedTime": "2020-01-01T00:00:00Z"}

    async def page_thumbnail(
        self, presentation_id: str, page_object_id: str, *, size: str = "MEDIUM"
    ) -> str:
        return "https://thumb.example/lib_L1.png"


@pytest.fixture
def put_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(pack_api.settings, "pack_dir", str(tmp_path))
    monkeypatch.setattr(pack_api.settings, "pack_name", "nsw-property")
    monkeypatch.setattr(pack_api.settings, "google_slides_template_id", "slides-1")
    monkeypatch.setattr(pack_api.settings, "google_sheet_template_id", "sheet-1")
    monkeypatch.setattr(pack_api, "credentials_present", lambda: True)
    _write_pack_json(tmp_path)  # a prior sync must already exist to edit
    return tmp_path


def test_put_writes_only_the_changed_cell_and_resyncs(put_env: Path) -> None:
    fake = FakePutClient()
    app.dependency_overrides[get_google_client] = lambda: fake
    try:
        resp = client.put("/agent/pack/layouts/L1", json={"use_when": "a brand new sentence"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["fingerprint_changed"] is True
        l1 = next(layout for layout in body["layouts"] if layout["id"] == "L1")
        assert l1["use_when"] == "a brand new sentence"

        # Only the changed cell was written — `enabled` was untouched.
        assert len(fake.writes) == 1
        a1, values = fake.writes[0]
        assert a1 == "'_pack'!D2"
        assert values == [["a brand new sentence"]]

        # pack.json on disk now reflects the write, not the pre-PUT snapshot.
        on_disk = json.loads((put_env / "nsw-property" / "pack.json").read_text())
        assert on_disk["layouts"][0]["use_when"] == "a brand new sentence"
    finally:
        app.dependency_overrides.clear()


def test_put_enabled_writes_the_enabled_cell(put_env: Path) -> None:
    fake = FakePutClient()
    app.dependency_overrides[get_google_client] = lambda: fake
    try:
        resp = client.put("/agent/pack/layouts/L1", json={"enabled": False})
        assert resp.status_code == 200
        assert fake.writes == [("'_pack'!C2", [["FALSE"]])]
    finally:
        app.dependency_overrides.clear()


def test_put_busts_the_sdk_agent_catalogue_cache(put_env: Path) -> None:
    from agent import sdk_agent

    sdk_agent._catalogue_cache["slides-1"] = ()
    sdk_agent._pack_catalogue_cache["slides-1"] = ()

    fake = FakePutClient()
    app.dependency_overrides[get_google_client] = lambda: fake
    try:
        resp = client.put("/agent/pack/layouts/L1", json={"use_when": "x"})
        assert resp.status_code == 200
        assert sdk_agent._catalogue_cache == {}
        assert sdk_agent._pack_catalogue_cache == {}
    finally:
        app.dependency_overrides.clear()


def test_put_unknown_layout_id_is_404(put_env: Path) -> None:
    fake = FakePutClient()
    app.dependency_overrides[get_google_client] = lambda: fake
    try:
        resp = client.put("/agent/pack/layouts/L999", json={"use_when": "x"})
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_put_without_credentials_is_503(put_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pack_api, "credentials_present", lambda: False)
    fake = FakePutClient()
    app.dependency_overrides[get_google_client] = lambda: fake
    try:
        resp = client.put("/agent/pack/layouts/L1", json={"use_when": "x"})
        assert resp.status_code == 503
    finally:
        app.dependency_overrides.clear()
