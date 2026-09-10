#!/usr/bin/env python3
"""Mint the one Google refresh token the deck export needs (s46 M2).

Run once, on your own machine, with your own Google account. The token it mints
goes in `.env` as GOOGLE_DECK_REFRESH_TOKEN and is the *only* Google credential
the agent ever holds — there is no interactive flow in the app, and prod/demo
(which runs no data-agent at all) never receives it.

The DECK_ prefix matters: `GOOGLE_CLIENT_ID` is already the backend's Google
Sign-in **Web** client. This is a second, separate OAuth client of type Desktop
app, because only that type may use the loopback redirect this flow needs.

Before running, once, in a Google Cloud project you own:

  1. Enable three APIs: Google Slides, Google Sheets, Google Drive.
  2. APIs & Services -> Credentials -> Create credentials -> OAuth client ID,
     application type **Desktop app**. Copy the client id and secret.
  3. If the consent screen asks for a user type, choose Internal when the project
     belongs to a Workspace org (no verification is ever required), or External
     + add yourself as a test user for a personal @gmail.com account.

Then put the two values in `.env`:

    GOOGLE_DECK_CLIENT_ID=<...>.apps.googleusercontent.com
    GOOGLE_DECK_CLIENT_SECRET=<...>

and run:

    uv run python scripts/google_auth.py --write-env

`--write-env` reads those two from `.env` and writes the refresh token back into
it (file permissions 0600), so no secret is ever typed on a command line (where
it lands in shell history) or pasted into a chat window. Without the flag the
three lines are written to a private 0600 temp file instead of stdout, and the
client id/secret must come from the environment.

Scopes requested are deliberately narrow:
  * drive.file    — per-file access to files this app creates. NOT `drive`,
                    which is a "restricted" scope needing a security assessment.
                    `permissions.create` accepts drive.file, so public sharing of
                    our own artifacts needs nothing broader.
  * spreadsheets  — create the Sheet, write ranges, add native charts.
  * presentations — copy the pack, create slides, embed the charts.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import secrets
import socketserver
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 — public endpoint
SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/presentations",
]
# Preferred loopback port, with port 0 (kernel-assigned) as the fallback when
# something else already holds it. Safe to vary because a Desktop-app OAuth
# client accepts *any* http://localhost port — Google matches loopback redirects
# on host only, deliberately, so installed apps need no fixed port. A Web client
# would reject this; that is one more reason the deck credential must be Desktop.
PREFERRED_PORT = 8765

_result: dict[str, str] = {}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's interface
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        _result.update({k: v[0] for k, v in params.items()})
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in _result
        body = (
            "<h2>Authorised.</h2><p>You can close this tab and return to the terminal.</p>"
            if ok
            else f"<h2>Authorisation failed.</h2><pre>{_result.get('error', 'unknown')}</pre>"
        )
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args: object) -> None:
        """Silence the default request logging — the terminal output is the UI."""


def _read_env(path: Path) -> dict[str, str]:
    """KEY=value pairs from a `.env`, ignoring comments and blanks."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def _write_env(path: Path, values: dict[str, str]) -> None:
    """Set each KEY=value in `.env`, replacing an existing line for that key.

    Deliberately line-oriented rather than a dotenv round-trip: the file is full
    of comments the user wrote, and rewriting it through a parser would lose
    them. Anything not being set is copied through byte-for-byte.
    """
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(values)
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.append("# s46 Google Slides/Sheets export (scripts/google_auth.py)")
        out.extend(f"{k}={v}" for k, v in remaining.items())
    path.write_text("\n".join(out) + "\n")
    path.chmod(0o600)


def _post_form(url: str, data: dict[str, str]) -> dict[str, object]:
    encoded = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=encoded)  # noqa: S310 — constant https URL
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        return dict(json.loads(resp.read().decode()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-env",
        metavar="PATH",
        nargs="?",
        const=".env",
        help="write the three lines into this .env instead of printing them (default: .env)",
    )
    args = parser.parse_args()

    # `.env` first-class, not just the shell: putting the client id and secret
    # straight into the file that already holds every other secret means neither
    # ever has to be typed on a command line (where it lands in shell history).
    env_file = _read_env(Path(args.write_env or ".env"))
    client_id = (
        os.environ.get("GOOGLE_DECK_CLIENT_ID") or env_file.get("GOOGLE_DECK_CLIENT_ID", "")
    ).strip()
    client_secret = (
        os.environ.get("GOOGLE_DECK_CLIENT_SECRET") or env_file.get("GOOGLE_DECK_CLIENT_SECRET", "")
    ).strip()
    if not client_id or not client_secret:
        print(
            "Set GOOGLE_DECK_CLIENT_ID and GOOGLE_DECK_CLIENT_SECRET first — in .env "
            "or in the environment (OAuth client of type 'Desktop app'; see this "
            "file's docstring). These are NOT the GOOGLE_CLIENT_ID used for sign-in.",
            file=sys.stderr,
        )
        return 2

    state = secrets.token_urlsafe(16)
    try:
        httpd = socketserver.TCPServer(("127.0.0.1", PREFERRED_PORT), _Handler)
    except OSError:
        # Something else holds the preferred port. Let the kernel pick one rather
        # than asking the user to go kill whatever it is.
        httpd = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    redirect_uri = f"http://localhost:{httpd.server_address[1]}/"

    auth_url = f"{AUTH_URL}?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            # offline + consent is what actually returns a refresh_token; without
            # prompt=consent Google omits it on a repeat authorisation.
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )

    with httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        print(f"Listening on {redirect_uri}")
        print(f"Opening your browser to authorise.\nIf it does not open:\n\n  {auth_url}\n")
        webbrowser.open(auth_url)
        while "code" not in _result and "error" not in _result:
            httpd.handle_request()
        httpd.shutdown()

    if "code" not in _result:
        print(f"Authorisation failed: {_result.get('error')}", file=sys.stderr)
        return 1
    if _result.get("state") != state:
        print("State mismatch — aborting rather than trusting the response.", file=sys.stderr)
        return 1

    tokens = _post_form(
        TOKEN_URL,
        {
            "code": _result["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            # Must match the authorisation request byte-for-byte, so reuse the
            # port that was actually bound rather than the preferred one.
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    refresh = tokens.get("refresh_token")
    if not refresh:
        print(
            "No refresh_token returned. Revoke this app at "
            "https://myaccount.google.com/permissions and run again.",
            file=sys.stderr,
        )
        return 1

    values = {
        "GOOGLE_DECK_CLIENT_ID": client_id,
        "GOOGLE_DECK_CLIENT_SECRET": client_secret,
        "GOOGLE_DECK_REFRESH_TOKEN": str(refresh),
    }
    if args.write_env:
        path = Path(args.write_env)
        _write_env(path, values)
        print(f"\nWrote GOOGLE_DECK_CLIENT_ID/_SECRET/_REFRESH_TOKEN to {path}.")
        print("The token itself was not printed — it is only in that file.")
    else:
        fd, tmp_path = tempfile.mkstemp(prefix="google-deck-env-", suffix=".txt")
        os.chmod(tmp_path, 0o600)
        with os.fdopen(fd, "w") as fh:
            for key, value in values.items():
                fh.write(f"{key}={value}\n")
        print(
            f"\nWrote GOOGLE_DECK_CLIENT_ID/_SECRET/_REFRESH_TOKEN to {tmp_path} (mode 0600)."
        )
        print("Copy those three lines into .env, then delete the file.")
    print("\nThen set DECK_EXPORT=1 (and DECK_PUBLIC=1 to share artifacts read-only),")
    print("and restart the data-agent: docker compose up -d --no-deps data-agent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
