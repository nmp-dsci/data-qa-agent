"""The quick sandbox executor (Phase A).

Runs model-written pandas over an injected DataFrame in a **spawned subprocess**
with:
  * a restricted ``__builtins__`` (no ``open`` / ``eval`` / ``exec`` / ``input``,
    and a guarded ``__import__`` allowing only preloaded/no-I/O modules), so the
    code can't open files or import ``os``;
  * network blocked (``socket`` disabled in the child);
  * no DB handle, secrets, or env passed into the code's namespace — only ``df``,
    ``pd`` and ``skills``;
  * CPU, memory, and wall-clock caps.

The model's code is expected to assign its report to ``result`` (the
``build_report`` output). Skills-used / skill-gaps travel back for per-run
telemetry (contract.py).

This is deliberately the *quick* sandbox to prove the win — a restricted-builtins
subprocess is not a hard isolation boundary against a determined escape. Phase B
replaces this executor with Pyodide/WASM (no syscalls) behind the same
``run_code`` signature; nothing else changes.
"""

from __future__ import annotations

import contextlib
import io
import multiprocessing as mp
import queue as _queue
import traceback
from typing import Any

import pandas as pd

from .contract import AnalysisResult, SkillGap, cap_stdout

# Resource caps for one run.
_CPU_SECONDS = 8
_WALLCLOCK_SECONDS = 12  # parent-side hard stop; > CPU cap to allow spawn/import

# Builtins the model code may use. Deliberately excludes open, eval, exec,
# compile, input, globals, locals, vars, memoryview, help — the obvious file /
# introspection escape hatches. __import__ is added back below, guarded to the
# allowlist. pandas and skills are already imported (they keep full builtins);
# this only limits the exec'd code.
_SAFE_BUILTIN_NAMES = (
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "divmod",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "getattr",
    "hasattr",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "print",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "slice",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
)


class SandboxError(RuntimeError):
    """Raised for infrastructure failures (timeout, crash) — not model code errors."""


# Modules an `import` statement may name. All are preloaded (or stdlib with no
# I/O surface), so allowing them grants no new capability — it only stops a
# reflexive `import pandas as pd` from killing the run. Discovered the hard way
# in the s45 gate: Claude habitually writes that line, the bare "__import__ not
# found" pointed at the *call site*, and the model burned its whole analysis
# budget bisecting the wrong function.
_IMPORTABLE_MODULES = frozenset({"pandas", "numpy", "math", "statistics", "datetime"})


def _guarded_import() -> Any:
    import builtins

    real_import = builtins.__import__

    def _limited(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.split(".")[0] in _IMPORTABLE_MODULES:
            return real_import(name, *args, **kwargs)
        raise ImportError(
            f"import of {name!r} is blocked in the sandbox — write import-free code: "
            "pd (pandas) and skills are preloaded, and only "
            f"{', '.join(sorted(_IMPORTABLE_MODULES))} may be imported"
        )

    return _limited


def _safe_builtins() -> dict[str, Any]:
    import builtins

    safe = {name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES}
    safe["True"], safe["False"], safe["None"] = True, False, None
    safe["__import__"] = _guarded_import()
    # A few exceptions the model may legitimately raise/catch.
    for exc in ("Exception", "ValueError", "KeyError", "TypeError", "ZeroDivisionError"):
        safe[exc] = getattr(builtins, exc)
    return safe


def _block_network() -> None:
    """Neutralise the socket module in the child so code can't reach the network."""
    import socket

    def _blocked(*_a: Any, **_k: Any) -> Any:
        raise SandboxError("network access is blocked in the sandbox")

    # setattr (not direct assignment) so mypy doesn't read this as rebinding the
    # socket *type*; the intent is to neutralise the module's connection surface.
    setattr(socket, "socket", _blocked)  # noqa: B010
    setattr(socket, "create_connection", _blocked)  # noqa: B010


def _apply_rlimits() -> None:
    """Best-effort CPU cap (POSIX). No-op where resource is unavailable.

    We deliberately do NOT cap RLIMIT_AS: numpy/pandas reserve gigabytes of
    *virtual* address space on import, so a tight AS cap leaves no room to
    allocate a new thread stack — the mp.Queue feeder thread then dies with
    "can't start new thread" and the result never returns. Hard memory isolation
    is Phase B's job (Pyodide/WASM caps heap natively); here the wall-clock stop
    plus the bounded (extract-time) frame keep memory in check.
    """
    try:
        import resource
    except ImportError:  # pragma: no cover - non-POSIX
        return
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (_CPU_SECONDS, _CPU_SECONDS))
    except (ValueError, OSError):  # pragma: no cover
        pass


def _child(  # pragma: no cover - subprocess
    out: mp.Queue[Any], code: str, frames: dict[str, pd.DataFrame]
) -> None:
    """Runs in the spawned process: harden, exec model code, return the result."""
    _apply_rlimits()
    _block_network()
    from .. import skills

    skills.reset()
    sandbox_globals: dict[str, Any] = {
        "__builtins__": _safe_builtins(),
        "pd": pd,
        "skills": skills,
        **frames,  # the governed extract(s), by name — usually just `df`
    }
    # s49 M0: capture what the code prints. `print` is in the safe builtins and
    # resolves sys.stdout at call time, so redirecting the module attribute is
    # enough — no need to hand the exec'd namespace a different print. Every
    # exit path reads the buffer, including the error one: the output leading
    # up to a traceback is usually the whole diagnosis.
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            exec(code, sandbox_globals)  # noqa: S102 - the whole point; namespace is locked down
        result = sandbox_globals.get("result")
        if not isinstance(result, dict):
            out.put(
                {
                    "error": "sandbox code must assign a report dict to `result` "
                    "(e.g. result = skills.build_report(...))",
                    "skills_used": skills.used(),
                    "skill_gaps": skills.gaps(),
                    "used_inline_math": skills.used_inline_math(),
                    "stdout": cap_stdout(buffer.getvalue()),
                }
            )
            return
        out.put(
            {
                "report": result,
                "skills_used": skills.used(),
                "skill_gaps": skills.gaps(),
                "frames": skills.capture_frames(sandbox_globals),
                "used_inline_math": skills.used_inline_math(),
                "stdout": cap_stdout(buffer.getvalue()),
            }
        )
    except Exception:  # noqa: BLE001 - report model-code errors so the model self-corrects
        out.put(
            {
                "error": traceback.format_exc(limit=4),
                "skills_used": skills.used(),
                "skill_gaps": skills.gaps(),
                "used_inline_math": skills.used_inline_math(),
                "stdout": cap_stdout(buffer.getvalue()),
            }
        )


def run_code(
    code: str,
    df: pd.DataFrame | None = None,
    *,
    frames: dict[str, pd.DataFrame] | None = None,
) -> AnalysisResult:
    """Execute model-written ``code`` over the injected frame(s) in the sandbox.

    Pass a single frame as ``df`` (bound to ``df`` in the code) or several as
    ``frames={"sales": ..., "rent": ...}`` for multi-extract skills like
    ``gross_yield``. Returns an :class:`AnalysisResult`. Model-code errors come
    back as ``result.error`` (so the agent can self-correct within its retry
    budget); a timeout or crashed worker is surfaced the same way rather than
    raising, so a broken run degrades to an error string instead of taking down
    the request.
    """
    all_frames = dict(frames or {})
    if df is not None:
        all_frames.setdefault("df", df)
    ctx = mp.get_context("spawn")
    out: mp.Queue[Any] = ctx.Queue()
    proc = ctx.Process(target=_child, args=(out, code, all_frames))
    proc.start()

    # Drain the queue BEFORE join: an mp.Queue put() is flushed by a feeder
    # thread, so joining first can race the child's exit and lose a small
    # payload. get(timeout=...) is also our wall-clock stop — an empty get means
    # the child hung or was killed by an RLIMIT before it could return anything.
    try:
        payload = out.get(timeout=_WALLCLOCK_SECONDS)
    except _queue.Empty:
        if proc.is_alive():
            proc.terminate()
            proc.join(2)
            if proc.is_alive():  # pragma: no cover - terminate should suffice
                proc.kill()
            return AnalysisResult(error=f"sandbox timed out after {_WALLCLOCK_SECONDS}s")
        # Exited without queuing a result ⇒ killed by an RLIMIT (CPU/memory).
        return AnalysisResult(
            error=f"sandbox worker exited without a result (code {proc.exitcode}); "
            "likely hit the CPU or memory cap"
        )
    finally:
        proc.join(2)
        if proc.is_alive():  # pragma: no cover
            proc.kill()

    gaps = [SkillGap(**g) for g in payload.get("skill_gaps", [])]
    return AnalysisResult(
        report=payload.get("report"),
        skills_used=payload.get("skills_used", []),
        skill_gaps=gaps,
        frames=payload.get("frames", []),
        used_inline_math=payload.get("used_inline_math", False),
        stdout=payload.get("stdout", ""),
        error=payload.get("error"),
    )
