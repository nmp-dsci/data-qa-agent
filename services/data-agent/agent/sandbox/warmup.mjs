// Build-time warm-up: load Pyodide once and fetch pandas/numpy so their wheels
// are cached into node_modules/pyodide. After this the sandbox runs fully offline
// (the container blocks network at run time). Mirrors the Dockerfile's fastembed
// pre-fetch. Non-fatal: a transient hiccup here degrades to a slow first run.
import { loadPyodide } from "pyodide";

const t0 = Date.now();
const py = await loadPyodide();
await py.loadPackage(["pandas", "numpy"]);
py.runPython("import pandas, numpy");
console.log(`[warmup] pyodide + pandas/numpy cached in ${Date.now() - t0}ms`);
