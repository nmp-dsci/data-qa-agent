"""s41 D1 (fallback): per-container CPU/memory -> Prometheus, via the Docker API.

cAdvisor v0.49 cannot name containers on Docker Desktop (its legacy Docker
client fails the daemon's info handshake, so every series is an anonymous
cgroup id). This exporter does the one thing we need instead: poll
/containers/json + /containers/<id>/stats?one-shot=true over the mounted
docker.sock, compute CPU%% from consecutive samples exactly the way
`docker stats` does, and serve:

    dataqa_container_cpu_percent{name, service}   %% of one core
    dataqa_container_mem_mb{name, service}        working-set MB

Stdlib only; runs from a stock python:alpine with the script bind-mounted.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DOCKER_SOCK = "/var/run/docker.sock"
POLL_S = 5
PORT = 9110

_lines: list[str] = []
_prev: dict[str, tuple[int, int]] = {}  # id -> (cpu_total_ns, system_ns)


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self) -> None:
        super().__init__("localhost")

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect(DOCKER_SOCK)
        self.sock = sock


def _docker_get(path: str) -> Any:
    conn = _UnixHTTPConnection()
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return json.loads(resp.read())
    finally:
        conn.close()


def _sample() -> list[str]:
    lines = [
        "# TYPE dataqa_container_cpu_percent gauge",
        "# TYPE dataqa_container_mem_mb gauge",
    ]
    for container in _docker_get("/containers/json"):
        cid = container["Id"]
        name = (container.get("Names") or ["/?"])[0].lstrip("/")
        service = container.get("Labels", {}).get("com.docker.compose.service", "")
        try:
            stats = _docker_get(f"/containers/{cid}/stats?stream=false&one-shot=true")
        except (OSError, ValueError):
            continue
        cpu = stats.get("cpu_stats", {})
        cpu_total = cpu.get("cpu_usage", {}).get("total_usage", 0)
        system = cpu.get("system_cpu_usage", 0)
        pct = 0.0
        if cid in _prev and system:
            dc, ds = cpu_total - _prev[cid][0], system - _prev[cid][1]
            if ds > 0:
                # Same formula as `docker stats`: share of total machine CPU,
                # scaled by core count => "% of one core" semantics.
                pct = dc / ds * cpu.get("online_cpus", 1) * 100
        _prev[cid] = (cpu_total, system)
        mem = stats.get("memory_stats", {})
        mem_mb = (mem.get("usage", 0) - mem.get("stats", {}).get("inactive_file", 0)) / 1048576
        labels = f'{{name="{name}",service="{service}"}}'
        lines.append(f"dataqa_container_cpu_percent{labels} {pct:.2f}")
        lines.append(f"dataqa_container_mem_mb{labels} {mem_mb:.1f}")
    return lines


def _poll_forever() -> None:
    global _lines
    while True:
        try:
            _lines = _sample()
        except (OSError, ValueError) as exc:
            print(f"[stats-exporter] sample failed: {exc}", flush=True)
        time.sleep(POLL_S)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler contract
        body = ("\n".join(_lines) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # quiet: scraped every 5s
        pass


if __name__ == "__main__":
    threading.Thread(target=_poll_forever, daemon=True).start()
    print(f"[stats-exporter] serving :{PORT}, polling every {POLL_S}s", flush=True)
    ThreadingHTTPServer(("", PORT), _Handler).serve_forever()
