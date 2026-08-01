// Translate between the structured builder's `filter` field — a SQL predicate
// fragment the object builder ANDs onto the golden's own WHERE
// (agent/object_builder.py :: canonical_extract_sql) — and the Explore-style
// dimension chips a curator edits.
//
// The wire contract stays SQL text, so goldens authored before the chip editor
// still load unchanged, and a predicate the chips can't hold (a range, an OR, a
// comparison) round-trips untouched through the raw-SQL escape hatch instead of
// being silently dropped — dropping one would widen the object's rows.
import type { ExploreFilters } from "../../lib/api";

/** A SQL literal for a chip value. Domain and typeahead values arrive typed
 *  straight from the column (JSON numbers for numeric columns, strings for
 *  text), so the JS type is the right signal for whether to quote. */
function literal(v: string | number): string {
  return typeof v === "number" ? String(v) : `'${v.replace(/'/g, "''")}'`;
}

/** Chips -> the predicate the builder sends. A chip with nothing chosen yet is
 *  not a filter, so empty values are dropped rather than emitted as `col = ''`.
 *  An explicitly empty multi-select is different: it says "no values", so it
 *  emits `false` — the same thing the Explore backend compiles an empty IN to.
 *  Silently dropping it would widen the object to every row instead. */
export function filtersToSql(filters: ExploreFilters): string {
  const parts: string[] = [];
  for (const [col, raw] of Object.entries(filters)) {
    if (Array.isArray(raw) && raw.length === 0) {
      parts.push("false");
      continue;
    }
    const vals = (Array.isArray(raw) ? raw : [raw]).filter(
      (v): v is string | number => typeof v === "number" || (typeof v === "string" && v !== ""),
    );
    if (vals.length === 0) continue;
    parts.push(
      vals.length === 1
        ? `${col} = ${literal(vals[0])}`
        : `${col} in (${vals.map(literal).join(", ")})`,
    );
  }
  return parts.join(" and ");
}

/** Split on a top-level separator — outside single-quoted strings and outside
 *  parentheses, so `in ('a', 'b')` stays one part and a quoted `'sand and clay'`
 *  is never mistaken for a conjunction. `sep` is a bare word or a single char. */
function splitTop(sql: string, sep: string): string[] {
  const word = /^[a-z]+$/i.test(sep);
  const parts: string[] = [];
  let depth = 0;
  let quoted = false;
  let start = 0;
  let i = 0;
  while (i < sql.length) {
    const c = sql[i];
    if (quoted) {
      // '' inside a string literal is an escaped quote, not the closing one.
      if (c === "'" && sql[i + 1] === "'") i += 2;
      else {
        if (c === "'") quoted = false;
        i += 1;
      }
      continue;
    }
    if (c === "'") {
      quoted = true;
      i += 1;
      continue;
    }
    if (c === "(") {
      depth += 1;
      i += 1;
      continue;
    }
    if (c === ")") {
      depth = Math.max(0, depth - 1);
      i += 1;
      continue;
    }
    const hit = word
      ? sql.slice(i, i + sep.length).toLowerCase() === sep &&
        (i === 0 || !/[\w$]/.test(sql[i - 1])) &&
        !/[\w$]/.test(sql[i + sep.length] ?? " ")
      : c === sep;
    if (depth === 0 && hit) {
      parts.push(sql.slice(start, i));
      i += word ? sep.length : 1;
      start = i;
      continue;
    }
    i += 1;
  }
  parts.push(sql.slice(start));
  return parts.map((p) => p.trim()).filter((p) => p.length > 0);
}

/** A quoted string or a plain number, else null (anything else isn't a value a
 *  chip can hold). */
function parseLiteral(token: string): string | number | null {
  const t = token.trim();
  if (/^'(?:[^']|'')*'$/.test(t)) return t.slice(1, -1).replace(/''/g, "'");
  if (/^-?\d+(?:\.\d+)?$/.test(t)) return Number(t);
  return null;
}

const PREDICATE_RE = /^([a-z_][a-z0-9_]*)\s*(=|\bin\b)\s*([\s\S]+)$/i;

/** SQL -> chips, or null when the predicate isn't a plain AND of `col = value`
 *  / `col in (…)` tests over the given columns. Null is the signal to keep the
 *  raw-SQL box: the chips would not be able to represent it faithfully. */
export function filtersFromSql(sql: string, columns: Set<string>): ExploreFilters | null {
  const text = (sql ?? "").trim();
  if (!text) return {};
  const out: ExploreFilters = {};
  for (const part of splitTop(text, "and")) {
    const m = PREDICATE_RE.exec(part);
    if (!m) return null;
    const col = m[1].toLowerCase();
    const op = m[2].toLowerCase();
    const rest = m[3].trim();
    // One chip per column: `a = 1 and a = 2` has no chip representation.
    if (!columns.has(col) || col in out) return null;
    if (op === "=") {
      const v = parseLiteral(rest);
      if (v === null) return null;
      out[col] = v;
      continue;
    }
    if (!rest.startsWith("(") || !rest.endsWith(")")) return null;
    const vals: (string | number)[] = [];
    for (const item of splitTop(rest.slice(1, -1), ",")) {
      const v = parseLiteral(item);
      if (v === null) return null;
      vals.push(v);
    }
    if (vals.length === 0) return null;
    out[col] = vals;
  }
  return out;
}
