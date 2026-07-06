// Pyodide/WASM host for the hardened sandbox (restructure Phase B).
//
// Runs one run_analysis job inside Pyodide (CPython compiled to WebAssembly):
// there are no syscalls to escape, no host filesystem, and no network reachable
// from the model's Python — a strictly stronger isolation boundary than the
// Phase A subprocess. The Python runner (pyodide_runner.py) spawns this once per
// run, writes a job as JSON on stdin, and reads one JSON result line on stdout.
//
// Job  (stdin) : {code, frames:{name:{columns,rows}}, safe_builtins:[...]}
// Result(stdout): {report|null, error|null, skills_used:[...], skill_gaps:[...],
//                  used_inline_math:bool}
//
// pandas/numpy load offline from node_modules/pyodide (baked into the image at
// build time); nothing is fetched at run time.
import { loadPyodide } from "pyodide";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const AGENT_DIR = process.env.AGENT_DIR || join(HERE, ".."); // agent/ package root

// The pure-Python + pandas subset of the package the skills need. Read from the
// real source tree so there is exactly one copy of the skills.
const PKG_FILES = [
  "__init__.py",
  "analytics.py",
  "chart.py",
  "skills/__init__.py",
  "skills/analysis.py",
  "skills/charts.py",
  "skills/reporting.py",
];

function readStdin() {
  return new Promise((resolve, reject) => {
    let buf = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (d) => (buf += d));
    process.stdin.on("end", () => resolve(buf));
    process.stdin.on("error", reject);
  });
}

// The in-Pyodide bootstrap: rebuild frames, lock down builtins, exec model code,
// and return telemetry + report as a JSON string. `job_json` is injected below.
const BOOTSTRAP = `
import json
import pandas as pd
from agent import skills

_job = json.loads(job_json)
_frames = {
    name: pd.DataFrame(f["rows"], columns=f["columns"])
    for name, f in _job["frames"].items()
}

import builtins as _b
_safe = {n: getattr(_b, n) for n in _job["safe_builtins"] if hasattr(_b, n)}
_safe["True"], _safe["False"], _safe["None"] = True, False, None
for _e in ("Exception", "ValueError", "KeyError", "TypeError", "ZeroDivisionError", "IndexError"):
    _safe[_e] = getattr(_b, _e)

skills.reset()
_g = {"__builtins__": _safe, "pd": pd, "skills": skills, **_frames}
_out = {"report": None, "error": None}
try:
    exec(_job["code"], _g)
    _res = _g.get("result")
    if isinstance(_res, dict):
        _out["report"] = _res
    else:
        _out["error"] = (
            "sandbox code must assign a report dict to \`result\` "
            "(e.g. result = skills.build_report(...))"
        )
except Exception:
    import traceback
    _out["error"] = traceback.format_exc(limit=4)

_out["skills_used"] = skills.used()
_out["skill_gaps"] = skills.gaps()
_out["used_inline_math"] = skills.used_inline_math()
json.dumps(_out, default=str)
`;

async function main() {
  const jobText = await readStdin();

  const pyodide = await loadPyodide();
  await pyodide.loadPackage(["pandas", "numpy"]);

  // Mount the skill package into Pyodide's in-memory FS and make it importable.
  pyodide.FS.mkdirTree("/pkg/agent/skills");
  for (const rel of PKG_FILES) {
    const body = readFileSync(join(AGENT_DIR, rel), "utf8");
    pyodide.FS.writeFile(`/pkg/agent/${rel}`, body);
  }
  pyodide.runPython('import sys; sys.path.insert(0, "/pkg")');

  pyodide.globals.set("job_json", jobText);
  const resultJson = await pyodide.runPythonAsync(BOOTSTRAP);
  process.stdout.write(resultJson + "\n");
}

main().catch((err) => {
  // Infrastructure failure (not model-code error): surface as an error result so
  // the Python side degrades to a fixable message instead of a hard crash.
  process.stdout.write(
    JSON.stringify({
      report: null,
      error: "pyodide host failure: " + (err && err.message ? err.message : String(err)),
      skills_used: [],
      skill_gaps: [],
      used_inline_math: false,
    }) + "\n",
  );
  process.exit(0);
});
