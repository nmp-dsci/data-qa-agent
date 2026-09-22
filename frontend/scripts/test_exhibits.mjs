// Unit test for src/lib/exhibits.ts's key scheme (s52). No vitest in this
// project, so this is node:test + Node's built-in TypeScript stripping:
//
//   npm run test:exhibits        (node --experimental-strip-types …)
//
// The vectors live in ../tests/exhibit_key_vectors.json and are shared with
// tests/test_export_exhibits.py, which pins the Python exporter to the same
// outputs — the file scheme is a contract between the two.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { exhibitKey, exhibitUrl } from "../src/lib/exhibits.ts";

const here = dirname(fileURLToPath(import.meta.url));
const vectorsPath = resolve(here, "..", "..", "tests", "exhibit_key_vectors.json");
const { vectors } = JSON.parse(readFileSync(vectorsPath, "utf8"));

test("exhibitKey matches every shared vector", () => {
  assert.ok(vectors.length >= 10, "vectors file looks empty");
  for (const [path, expected] of vectors) {
    assert.equal(exhibitKey(path), expected, `key for ${path}`);
  }
});

test("exhibitUrl is rooted on the given origin, never the API base", () => {
  // `=` / `&` stay literal in the path (Vite's static middleware does not
  // decode %3D for public files); everything else is encodeURIComponent-ed.
  assert.equal(
    exhibitUrl("/admin/eval-runs?limit=50", "https://demo.example"),
    "https://demo.example/exhibits/admin/eval-runs__limit=50.json",
  );
  assert.equal(
    exhibitUrl("/architecture/content?kind=claude_md&name=", "https://demo.example"),
    "https://demo.example/exhibits/architecture/content__kind=claude_md&name=.json",
  );
  assert.equal(
    exhibitUrl("/admin/knowledge/domains%2Fx%2Fy.md", "http://localhost:5230/"),
    "http://localhost:5230/exhibits/admin/knowledge/domains/x/y.md.json",
  );
});
