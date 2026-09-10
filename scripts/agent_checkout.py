"""Reproduce a registered agent build's prompts/skills/knowledge (s49 M1).

    make agent-checkout FP=av-...

Looks up the ``data-qa-agent`` model version tagged with that fingerprint,
downloads its ``bundle.json`` (logged by ``mlflow_registry.py cmd_ensure`` to
the register run, see ``bundle_run_id``), and reproduces the build under
``.worktrees/<fp>/``:

* when the bundle knows a ``git_sha``, ``git worktree add`` checks out that
  commit directly — the preferred path, since it also gives you the rest of
  the repo (backend-api, tests, everything) at that point in time, not just
  the bundled surfaces;
* otherwise (a fingerprint minted from uncommitted local work has no
  reachable commit) the ``bundle.tar.gz`` artifact — a snapshot of
  ``agent/prompts/``, ``agent/skills/``, and ``knowledge/`` taken at register
  time — is extracted into ``.worktrees/<fp>/bundle/`` instead.

Either way this prints the compose override to run the reproduced build
(``AGENT_WORKTREE=...``) and the DB state note below — it never writes to the
database itself.

DB SNAPSHOT NOTE: ``bundle.json``'s ``db_snapshots`` only carries content
HASHES of the curator-editable DB layers (``app.dataset_ordinals``,
``app.knowledge_pages``) for drift detection, not the row content itself —
restoring those tables to their exact historical state is not something this
bundle can do. This script prints what it knows (the expected hash and how to
compare the live DB against it) instead of guessing at row content.
"""

from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mlflow_client as mc  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKTREES_DIR = REPO_ROOT / ".worktrees"


def _find_bundle_run(fingerprint: str) -> tuple[str, str]:
    """(model_version, bundle_run_id) for a fingerprint, or exit with a clear error."""
    for v in mc.search_model_versions(mc.MODEL_NAME):
        tags = {t["key"]: t["value"] for t in v.get("tags", [])}
        if tags.get("fingerprint") != fingerprint:
            continue
        bundle_run_id = tags.get("bundle_run_id")
        if not bundle_run_id:
            sys.exit(
                f"agent_checkout: v{v['version']} ({fingerprint}) has no bundle_run_id tag — "
                "it was registered before the s49 M1 bundle feature shipped. Re-run "
                "`make register` after rebuilding that fingerprint, or pick a newer one "
                "(`make status`)."
            )
        return v["version"], bundle_run_id
    sys.exit(
        f"agent_checkout: no {mc.MODEL_NAME} model version tagged fingerprint={fingerprint!r} "
        "— run `make status` to see what's registered, or `make register` to sync "
        "app.agent_versions first."
    )


def _git_worktree_add(dest: Path, git_sha: str) -> bool:
    """True on success; False (not a hard failure) when the sha isn't reachable
    from this clone — the caller falls back to the tarball."""
    if dest.exists():
        print(f"{dest} already exists — reusing it (remove it to force a fresh checkout).")
        return True
    proc = subprocess.run(
        ["git", "worktree", "add", "--detach", str(dest), git_sha],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(
            f"git worktree add failed for {git_sha} ({proc.stderr.strip()}) — "
            "falling back to the bundle tarball.",
            file=sys.stderr,
        )
        return False
    print(f"git worktree checked out at {dest} ({git_sha})")
    return True


def _extract_tarball(run_id: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    raw = mc.get_artifact_bytes(run_id, "bundle.tar.gz")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        tar.extractall(dest, filter="data")  # noqa: S202 — our own artifact, trusted content
    print(f"bundle tarball extracted to {dest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("fingerprint", metavar="FP", help="e.g. av-c0b82300d1cd")
    args = parser.parse_args()
    fp = args.fingerprint

    version, bundle_run_id = _find_bundle_run(fp)
    bundle = mc.get_artifact_json(bundle_run_id, "bundle.json")
    print(f"{mc.MODEL_NAME} v{version} · {fp} · bundle_run_id={bundle_run_id}")
    print(f"components: {bundle.get('components')}")

    dest = WORKTREES_DIR / fp
    WORKTREES_DIR.mkdir(exist_ok=True)

    git_sha = bundle.get("git_sha") or ""
    used_git = bool(git_sha) and _git_worktree_add(dest, git_sha)
    if used_git:
        agent_root = dest / "services" / "data-agent"
        override = f"AGENT_WORKTREE={dest.relative_to(REPO_ROOT)}"
    else:
        bundle_dir = dest / "bundle"
        _extract_tarball(bundle_run_id, bundle_dir)
        agent_root = bundle_dir
        override = f"AGENT_WORKTREE={bundle_dir.relative_to(REPO_ROOT)}"

    print(f"\nfiles reproduced under {agent_root}:")
    for f in bundle.get("files", []):
        print(f"  {f}")

    print(
        "\nTo run this build, mount it over the live agent code, e.g.:\n"
        f"  {override} docker compose -f docker-compose.yml -f docker-compose.worktree.yml "
        "up -d --no-deps data-agent agent-worker\n"
        "(a docker-compose.worktree.yml bind-mounting $AGENT_WORKTREE/agent over "
        "/app/agent is not shipped by this script — add one, or copy the reproduced "
        "files over services/data-agent/agent/ by hand for a quick local check.)"
    )

    db = bundle.get("db_snapshots") or {}
    print(
        "\nDB state at register time (NOT restored — this script never writes to the "
        "database; bundle.json only carries content hashes for drift detection, not "
        "row content):"
    )
    print(f"  app.dataset_ordinals  expected hash: {db.get('ordinals')}")
    print(
        "    -- compare with: SELECT d.slug, o.column_name, o.ordered_values FROM "
        "app.dataset_ordinals o JOIN app.datasets d ON d.id = o.dataset_id ORDER BY 1, 2;"
    )
    kp_hash = db.get("knowledge_pages")
    if kp_hash:
        print(f"  app.knowledge_pages   expected hash: {kp_hash}")
        print(
            "    -- compare with: SELECT path, version, body FROM app.knowledge_pages "
            "ORDER BY path;"
        )
    else:
        print(
            "  app.knowledge_pages   no curator overrides at register time (file-only knowledge)."
        )


if __name__ == "__main__":
    main()
