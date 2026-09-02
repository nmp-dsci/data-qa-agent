"""Minimal MLflow REST client — stdlib only (s43).

The eval and registry scripts follow eval_run.py's grain: no third-party
dependencies, subprocess/urllib against services the compose stack already
runs. The MLflow tracking server is just HTTP, so this speaks its REST API
directly instead of pulling the mlflow package into the root project.

Base URL from MLFLOW_URL (default = the compose host port, 5500).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

MLFLOW_URL = os.environ.get("MLFLOW_URL", "http://localhost:5500").rstrip("/")

TRACES_EXPERIMENT = "data-qa/traces"
EVALS_EXPERIMENT = "data-qa/evals"
MODEL_NAME = "data-qa-agent"
CHAMPION = "champion"
CHALLENGER = "challenger"


class MlflowError(RuntimeError):
    """The tracking server refused or was unreachable."""


def _now_ms() -> int:
    return int(time.time() * 1000)


def api(method: str, path: str, body: Any = None, *, ok404: bool = False) -> Any:
    """One REST call. ok404 turns RESOURCE_DOES_NOT_EXIST into None."""
    url = f"{MLFLOW_URL}/api/2.0/mlflow/{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        if ok404 and exc.code in (400, 404) and "RESOURCE_DOES_NOT_EXIST" in detail:
            return None
        raise MlflowError(f"{method} {path}: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise MlflowError(f"{method} {path}: {exc.reason} (is the mlflow service up?)") from exc


# ---- experiments -----------------------------------------------------------


def get_experiment_id(name: str) -> str | None:
    got = api(
        "GET",
        f"experiments/get-by-name?experiment_name={urllib.parse.quote(name)}",
        ok404=True,
    )
    return got["experiment"]["experiment_id"] if got else None


def ensure_experiment(name: str) -> str:
    existing = get_experiment_id(name)
    if existing is not None:
        return existing
    return api("POST", "experiments/create", {"name": name})["experiment_id"]


# ---- runs ------------------------------------------------------------------


def start_run(experiment_id: str, run_name: str, tags: dict[str, Any] | None = None) -> str:
    body = {
        "experiment_id": experiment_id,
        "run_name": run_name,
        "start_time": _now_ms(),
        "tags": [{"key": k, "value": str(v)} for k, v in (tags or {}).items() if v is not None],
    }
    return api("POST", "runs/create", body)["run"]["info"]["run_id"]


def log_batch(
    run_id: str,
    params: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    tags: dict[str, Any] | None = None,
) -> None:
    now = _now_ms()
    api(
        "POST",
        "runs/log-batch",
        {
            "run_id": run_id,
            "params": [
                {"key": k, "value": str(v)[:6000]}
                for k, v in (params or {}).items()
                if v is not None
            ],
            "metrics": [
                {"key": k, "value": float(v), "timestamp": now, "step": 0}
                for k, v in (metrics or {}).items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            ],
            "tags": [
                {"key": k, "value": str(v)[:5000]} for k, v in (tags or {}).items() if v is not None
            ],
        },
    )


def end_run(run_id: str, status: str = "FINISHED") -> None:
    api("POST", "runs/update", {"run_id": run_id, "status": status, "end_time": _now_ms()})


# ---- model registry --------------------------------------------------------


def ensure_registered_model(name: str = MODEL_NAME) -> None:
    got = api("GET", f"registered-models/get?name={urllib.parse.quote(name)}", ok404=True)
    if got is None:
        api("POST", "registered-models/create", {"name": name})


def search_model_versions(name: str = MODEL_NAME) -> list[dict[str, Any]]:
    filt = urllib.parse.quote(f"name='{name}'")
    got = api("GET", f"model-versions/search?filter={filt}&max_results=200")
    return got.get("model_versions", [])


def create_model_version(
    name: str,
    source: str,
    run_id: str | None = None,
    tags: dict[str, Any] | None = None,
    description: str = "",
) -> str:
    body: dict[str, Any] = {
        "name": name,
        "source": source,
        "description": description,
        "tags": [{"key": k, "value": str(v)} for k, v in (tags or {}).items() if v is not None],
    }
    if run_id:
        body["run_id"] = run_id
    return api("POST", "model-versions/create", body)["model_version"]["version"]


def set_alias(name: str, alias: str, version: str) -> None:
    api("POST", "registered-models/alias", {"name": name, "alias": alias, "version": str(version)})


def delete_alias(name: str, alias: str) -> None:
    q = f"registered-models/alias?name={urllib.parse.quote(name)}&alias={urllib.parse.quote(alias)}"
    try:
        api("DELETE", q)
    except MlflowError:
        pass  # alias absent — deleting it is a no-op


def get_alias_version(name: str, alias: str) -> str | None:
    q = f"registered-models/alias?name={urllib.parse.quote(name)}&alias={urllib.parse.quote(alias)}"
    try:
        got = api("GET", q, ok404=True)
    except MlflowError as exc:
        # A missing alias is INVALID_PARAMETER_VALUE ("... not found"), not
        # RESOURCE_DOES_NOT_EXIST — treat it as absence, not failure.
        if "not found" in str(exc).lower():
            return None
        raise
    return got["model_version"]["version"] if got else None
