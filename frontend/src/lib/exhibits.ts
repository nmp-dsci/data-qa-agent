// s52: static exhibits for the DB-less demo.
//
// The deployed demo runs with no database at all, so the read-only exhibit
// tabs (Goldens, Evaluations, Operations, Architecture, Admin, Settings' access
// panel) cannot ask the API for their data — every DB-backed route is simply
// not mounted there (404). Instead `scripts/export_exhibits.py` dumps each
// route the tabs read, once, from the local dev stack into
// `frontend/public/exhibits/`, and in demo mode the read helpers in api.ts
// fetch that JSON from the SPA's own origin instead of the API.
//
// The key scheme below is the contract with the exporter — the Python side
// (`scripts/export_exhibits.py::exhibit_key`) implements the same function and
// `tests/test_export_exhibits.py` pins both to the same vectors:
//
//   exhibits/<path without leading slash>[__<canonical query>].json
//
//   * path segments are URL-DECODED, re-split on "/" (a knowledge page path
//     like `domains/x/y.md` arrives encoded as one segment and lands as a
//     nested directory), then sanitised;
//   * canonical query = params sorted by key, `k=v` pairs joined by `&`, on
//     the DECODED values, then sanitised;
//   * sanitising replaces every character outside [A-Za-z0-9._=&-] with `_`.
//
//   /admin/eval-runs?limit=50                  -> exhibits/admin/eval-runs__limit=50.json
//   /admin/eval-goldens/abc-123                -> exhibits/admin/eval-goldens/abc-123.json
//   /architecture/content?kind=skill&name=period metric
//                                              -> exhibits/architecture/content__kind=skill&name=period_metric.json
//
// Params the dump cannot honour (a `since` timestamp computed at page load)
// are dropped from the key: the exporter writes the same route with the
// window it fetched at export time.

export const EXHIBIT_ROOT = "exhibits";

/** Query params dropped from the key — they are computed at page load, so no
 *  static file could match them. The exporter fetches these routes with the
 *  same window the page uses (7 days) as of export time. */
export const VOLATILE_QUERY_KEYS: readonly string[] = ["since"];

const SAFE = /[^A-Za-z0-9._=&-]/gu;

function sanitise(s: string): string {
  return s.replace(SAFE, "_");
}

function decodeLoose(s: string): string {
  try {
    return decodeURIComponent(s.replace(/\+/g, "%20"));
  } catch {
    return s;
  }
}

/** The static file key for a GET `path` (which may carry a query string),
 *  relative to the site root and without a leading slash. */
export function exhibitKey(path: string): string {
  const qIdx = path.indexOf("?");
  const rawPath = qIdx === -1 ? path : path.slice(0, qIdx);
  const rawQuery = qIdx === -1 ? "" : path.slice(qIdx + 1);

  const segments = rawPath
    .split("/")
    .map(decodeLoose)
    .join("/")
    .split("/")
    .filter((s) => s.length > 0)
    .map(sanitise);

  const pairs: [string, string][] = [];
  if (rawQuery) {
    for (const part of rawQuery.split("&")) {
      if (!part) continue;
      const eq = part.indexOf("=");
      const k = decodeLoose(eq === -1 ? part : part.slice(0, eq));
      const v = eq === -1 ? "" : decodeLoose(part.slice(eq + 1));
      if (VOLATILE_QUERY_KEYS.includes(k)) continue;
      pairs.push([k, v]);
    }
  }
  pairs.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  const canonical = sanitise(pairs.map(([k, v]) => `${k}=${v}`).join("&"));

  const base = `${EXHIBIT_ROOT}/${segments.join("/")}`;
  return canonical ? `${base}__${canonical}.json` : `${base}.json`;
}

/** One key segment, percent-encoded for the URL path. `=` and `&` are legal
 *  path characters (RFC 3986 sub-delims) and are kept literal on purpose:
 *  Vite's static middleware serves `eval-runs__limit=50.json` but not
 *  `…limit%3D50.json`, and S3 keys are matched the same way. Everything else
 *  a sanitised key can contain survives encodeURIComponent unchanged. */
function encodeSegment(seg: string): string {
  return encodeURIComponent(seg).replace(/%3D/gi, "=").replace(/%26/gi, "&");
}

/** Absolute URL of the static file on the SPA's OWN origin — never the API
 *  base. */
export function exhibitUrl(path: string, origin: string = globalThis.location?.origin ?? ""): string {
  const key = exhibitKey(path).split("/").map(encodeSegment).join("/");
  return `${origin.replace(/\/$/, "")}/${key}`;
}

// ---- demo switch -------------------------------------------------------------
// Flipped by api.ts once /auth/config answers. Readers await `demoExhibits()`
// rather than reading a boolean, because /me (session resume) and /auth/config
// race on a reload: a deep-linked exhibit tab could otherwise fire its first
// read before the app knows it is in demo mode and hit the (404) API instead.

let decided = false;
let value = false;
let resolveDecision: ((on: boolean) => void) | null = null;
const decision = new Promise<boolean>((resolve) => {
  resolveDecision = resolve;
});

export function setDemoExhibits(on: boolean): void {
  value = on;
  if (!decided) {
    decided = true;
    resolveDecision?.(on);
  }
}

/** Resolves once the auth config is known (either way). Instant after that. */
export function demoExhibits(): Promise<boolean> {
  return decided ? Promise.resolve(value) : decision;
}

/** Fetch one exhibit. Returns a real Response so callers keep their existing
 *  `if (!resp.ok) throw …` handling. The SPA host serves index.html (200,
 *  text/html) for unknown paths — the CloudFront SPA fallback and Vite's
 *  history fallback both do — so a non-JSON body is mapped to a 404 rather
 *  than handed to `resp.json()` to explode on. */
export async function fetchExhibit(path: string): Promise<Response> {
  let resp: Response;
  try {
    resp = await fetch(exhibitUrl(path), { headers: { Accept: "application/json" } });
  } catch {
    return new Response(null, { status: 503, statusText: "exhibit unreachable" });
  }
  const type = resp.headers.get("content-type") ?? "";
  if (!resp.ok || !/\bjson\b/i.test(type)) {
    return new Response(JSON.stringify({ detail: "exhibit_missing" }), {
      status: 404,
      statusText: "exhibit missing",
      headers: { "Content-Type": "application/json" },
    });
  }
  return resp;
}
