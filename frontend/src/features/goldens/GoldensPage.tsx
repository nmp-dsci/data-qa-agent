// Golden Answer (Builder) — author eval goldens (s46 simplified).
//
// Left: per-dataset list + New. Right: question/tier/tags header, the SQL
// extract (run → inspect rows, an optional chip-based additional filter), and
// the grader (draft → ready promotion). Save persists through
// /admin/eval-goldens; a `ready` golden is the benchmark the eval runner (E2)
// scores the agent against.
//
// The live object-preview, the page-column drag/arrange editor (ReportEditor)
// and the Structured Object Builder were removed with the report-engine
// (s46 presentation-handover) — Slides/Sheets are the report now, and eval
// goldens grade the SQL extract + grader spec, not a rendered presentation.
import { Star } from "lucide-react";
import { KitEmpty } from "@/components/kit/KitEmpty";
import { KitSelect } from "@/components/kit/KitSelect";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ExploreDataset,
  ExploreDimension,
  GoldenInput,
  GoldenListItem,
  GraderSpec,
  createGolden,
  deleteGolden,
  getAdminDatasets,
  getExploreDatasets,
  getGolden,
  listGoldens,
  prepGolden,
  updateGolden,
} from "../../lib/api";
import { Annunciator, Annunciators } from "../../ui/flightdeck";
import { SimpleTable } from "../../ui/SimpleTable";
import { BuilderFilter } from "./BuilderFilter";
import { GraderEditor } from "./GraderEditor";
import { graderColumns, graderIssue, pruneGrader } from "./graderSpec";

// The dataset list comes from the registry, not a literal (s24 M1). Hardcoding
// it silently locked nsw_yield — a registered dataset since migration 0025 —
// out of golden authoring, and left every yield golden mis-tagged nsw_sales.
const FALLBACK_DATASETS = ["nsw_sales", "nsw_rent"];
const TIERS = ["T1", "T2", "T3", "T4", "T5", "T6", "T7"];

interface Draft {
  id?: string;
  question: string;
  dataset: string;
  tier: string;
  as_user: string;
  tags: string[];
  holdout: boolean;
  authoring_status: string;
  golden_sql: string;
  /** The last successful SQL run, `{columns, rows}` — persisted as golden_data
   *  and what the grader validates its column names against. */
  golden_data: unknown;
  grader: GraderSpec;
}

const emptyDraft = (dataset: string): Draft => ({
  question: "",
  dataset,
  tier: "T1",
  as_user: "",
  tags: [],
  holdout: false,
  authoring_status: "draft",
  golden_sql: "",
  golden_data: null,
  grader: {},
});

// ---------------------------------------------------------------------------
// SQL formatting — purely cosmetic (whitespace + keyword case); the text still
// runs exactly the same, so the extract it produces is unchanged.
// ---------------------------------------------------------------------------
function splitTopLevel(s: string, sep = ","): string[] {
  const out: string[] = [];
  let depth = 0;
  let quote: string | null = null;
  let cur = "";
  for (const ch of s) {
    if (quote) {
      cur += ch;
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === "'" || ch === '"') quote = ch;
    else if (ch === "(") depth++;
    else if (ch === ")") depth--;
    if (ch === sep && depth === 0) {
      out.push(cur);
      cur = "";
    } else {
      cur += ch;
    }
  }
  if (cur.trim()) out.push(cur);
  return out;
}

// Clause keywords that start a new line — compound/qualified forms first so
// the longest match wins (e.g. "LEFT JOIN" beats "JOIN").
const SQL_MAJORS = [
  "LEFT OUTER JOIN",
  "RIGHT OUTER JOIN",
  "FULL OUTER JOIN",
  "LEFT JOIN",
  "RIGHT JOIN",
  "FULL JOIN",
  "INNER JOIN",
  "CROSS JOIN",
  "JOIN",
  "WITH",
  "SELECT",
  "FROM",
  "WHERE",
  "GROUP BY",
  "ORDER BY",
  "HAVING",
  "LIMIT",
  "OFFSET",
  "UNION ALL",
  "UNION",
  "ON",
];

function formatSql(raw: string): string {
  const sql = (raw ?? "").trim();
  if (!sql) return raw;
  const s = sql.replace(/\s+/g, " ");
  const upper = s.toUpperCase();
  let out = "";
  let depth = 0;
  let quote: string | null = null;
  let i = 0;
  while (i < s.length) {
    const ch = s[i];
    if (quote) {
      out += ch;
      if (ch === quote) quote = null;
      i++;
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      out += ch;
      i++;
      continue;
    }
    if (ch === "(") depth++;
    else if (ch === ")") depth = Math.max(0, depth - 1);
    if (ch !== "(" && ch !== ")" && depth === 0) {
      const kw = SQL_MAJORS.find((k) => {
        if (!upper.startsWith(k, i)) return false;
        const before = i === 0 ? " " : s[i - 1];
        const after = s[i + k.length] ?? " ";
        return /\s/.test(before) && /[\s(]/.test(after);
      });
      if (kw) {
        out = out.replace(/ $/, "");
        if (out) out += "\n";
        out += upper.slice(i, i + kw.length);
        i += kw.length;
        continue;
      }
    }
    out += ch;
    i++;
  }
  out = out.replace(
    /(^|\n)SELECT (DISTINCT )?([^\n]+)/i,
    (_m, lead: string, distinct: string | undefined, cols: string) => {
      const head = distinct ? `SELECT ${distinct.trim()}` : "SELECT";
      const parts = splitTopLevel(cols).map((c) => c.trim());
      if (parts.length < 2) return `${lead}${head} ${cols.trim()}`;
      return `${lead}${head}\n  ${parts.join(",\n  ")}`;
    },
  );
  return out.replace(/\n{2,}/g, "\n").trim();
}

// ---------------------------------------------------------------------------
// Additional-filter helpers — derive/apply the golden's WHERE clause so
// BuilderFilter's chip editor can offer it without a round trip. The mart
// table each dataset extracts from mirrors the backend MartProfile tables.
// ---------------------------------------------------------------------------
const TABLE_TO_DATASET: Record<string, string> = {
  "marts.property_sales": "nsw_sales",
  "marts.property_rent": "nsw_rent",
  "marts.property_yield": "nsw_yield",
};

/** The dataset a SQL extract actually queries, from its FROM clause (else null). */
function datasetFromSql(sql: string): string | null {
  const m = /\bfrom\s+(marts\.\w+)/i.exec(sql || "");
  const tbl = m?.[1]?.toLowerCase();
  return (tbl && TABLE_TO_DATASET[tbl]) || null;
}

/** The SQL's own WHERE predicate (whitespace-collapsed, or ""). */
function whereFromSql(sql: string): string {
  const m =
    /\bwhere\b([\s\S]*?)(?:\bgroup\s+by\b|\border\s+by\b|\bhaving\b|\blimit\b|\bwindow\b|;|$)/i.exec(
      sql || "",
    );
  return m ? m[1].replace(/\s+/g, " ").trim() : "";
}

/** Replace (or insert) the SQL's WHERE clause with `newWhere`. */
function setWhereClause(sql: string, newWhere: string): string {
  const trimmed = newWhere.trim();
  const clauseRe =
    /\bwhere\b[\s\S]*?(?=\bgroup\s+by\b|\border\s+by\b|\bhaving\b|\blimit\b|\bwindow\b|;|$)/i;
  if (clauseRe.test(sql || "")) {
    return (sql || "").replace(clauseRe, trimmed ? `WHERE ${trimmed} ` : "");
  }
  if (!trimmed) return sql;
  const boundary = /\b(group\s+by|order\s+by|having|limit|window)\b/i.exec(sql || "");
  if (boundary) {
    return `${sql.slice(0, boundary.index)}WHERE ${trimmed}\n${sql.slice(boundary.index)}`;
  }
  return `${(sql || "").trim()}\nWHERE ${trimmed}`;
}

// ---------------------------------------------------------------------------
// Small style constants + widgets
// ---------------------------------------------------------------------------
const box: React.CSSProperties = {
  border: "1px solid var(--border)",
  background: "var(--panel)",
  borderRadius: 10,
  padding: "12px 14px",
};
const mono: React.CSSProperties = {
  fontFamily: "var(--mono, ui-monospace, Menlo, monospace)",
  fontSize: 12.5,
};
const label: React.CSSProperties = {
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: 0.5,
  opacity: 0.7,
};
const fieldInput: React.CSSProperties = {
  fontSize: 12,
  padding: "3px 5px",
  width: "100%",
  fontFamily: "inherit",
  boxSizing: "border-box",
};

function btn(active = true): React.CSSProperties {
  return {
    border: "1px solid var(--border-2)",
    background: "var(--panel-2)",
    color: "var(--text)",
    borderRadius: 8,
    padding: "5px 12px",
    fontSize: 13,
    cursor: active ? "pointer" : "default",
    opacity: active ? 1 : 0.5,
  };
}

/** A tag chip editor — Enter (or blur) adds the typed tag. */
function TagsEditor({ tags, onChange }: { tags: string[]; onChange: (t: string[]) => void }) {
  const [input, setInput] = useState("");
  function add() {
    const v = input.trim();
    if (v && !tags.includes(v)) onChange([...tags, v]);
    setInput("");
  }
  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
      {tags.map((tag) => (
        <span key={tag} className="chip">
          {tag}{" "}
          <button
            type="button"
            aria-label={`Remove tag ${tag}`}
            style={{ border: "none", background: "none", color: "inherit", cursor: "pointer" }}
            onClick={() => onChange(tags.filter((x) => x !== tag))}
          >
            ×
          </button>
        </span>
      ))}
      <input
        value={input}
        placeholder="+ tag"
        onChange={(e) => setInput(e.target.value)}
        onBlur={add}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add();
          }
        }}
        style={{ ...fieldInput, width: 90 }}
      />
    </div>
  );
}

/** golden_data as stored/returned — either `{columns, rows}` or a bare array
 *  of row objects (older shape). Normalised for the SQL-stage table preview. */
function rowsFromGoldenData(data: unknown): { columns: string[]; rows: unknown[][] } | null {
  if (!data) return null;
  if (Array.isArray(data)) {
    const first = data[0];
    if (!first || typeof first !== "object") return null;
    const columns = Object.keys(first as Record<string, unknown>);
    return { columns, rows: data.map((r) => columns.map((c) => (r as Record<string, unknown>)[c])) };
  }
  const d = data as { columns?: unknown; rows?: unknown };
  if (Array.isArray(d.columns) && Array.isArray(d.rows)) {
    return { columns: d.columns.map(String), rows: d.rows as unknown[][] };
  }
  return null;
}

export function GoldensPage({
  seed,
}: {
  // Deep-link from a promoted chat answer: {id, nonce}. The nonce makes the
  // effect re-fire even when the same golden is promoted twice in a row.
  seed?: { id: string; nonce: number } | null;
}) {
  const [dataset, setDataset] = useState<string>("nsw_sales");
  // Tracks the latest selected dataset so an in-flight refresh() for a dataset
  // the curator has since navigated away from can detect it's stale and drop
  // its response instead of overwriting the current list.
  const datasetRef = useRef(dataset);
  datasetRef.current = dataset;
  const [datasets, setDatasets] = useState<string[]>(FALLBACK_DATASETS);
  const [list, setList] = useState<GoldenListItem[]>([]);
  const [draft, setDraft] = useState<Draft>(() => emptyDraft("nsw_sales"));
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [sqlError, setSqlError] = useState<string | null>(null);

  // The typed vocabulary (dimensions) BuilderFilter's chip editor offers —
  // derived from the SQL's own FROM table, so the filter always names columns
  // the extract actually has.
  const [vocab, setVocab] = useState<ExploreDataset[]>([]);
  useEffect(() => {
    let live = true;
    getExploreDatasets()
      .then((d) => live && setVocab(d))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  const builderDatasetSlug = datasetFromSql(draft.golden_sql) || draft.dataset;
  const builderDataset = vocab.find((d) => d.slug === builderDatasetSlug) ?? null;
  const filterDims: ExploreDimension[] =
    builderDataset?.dimensions.filter((d) => d.source === "mart" && d.kind !== "time") ?? [];

  const extract = rowsFromGoldenData(draft.golden_data);
  const graderCols = graderColumns(draft.golden_data);
  const graderBlocker = graderIssue(draft.grader, graderCols);

  // The authorable datasets are whatever the registry serves (s24 M1). On
  // failure the fallback list keeps the tab usable rather than empty.
  useEffect(() => {
    getAdminDatasets()
      .then((rows) => {
        const slugs = rows.map((d) => d.slug).filter(Boolean);
        if (slugs.length) setDatasets(slugs);
      })
      .catch(() => setDatasets(FALLBACK_DATASETS));
  }, []);

  const refresh = useCallback(async () => {
    const requestedDataset = dataset;
    try {
      const rows = await listGoldens(requestedDataset);
      if (requestedDataset !== datasetRef.current) return;
      setList(rows);
    } catch (e) {
      if (requestedDataset !== datasetRef.current) return;
      setMsg((e as Error).message);
    }
  }, [dataset]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const selectGolden = useCallback(async (id: string) => {
    setBusy("load");
    setMsg(null);
    setSqlError(null);
    try {
      const g = await getGolden(id);
      setDraft({
        id: g.id,
        question: g.question,
        dataset: g.dataset ?? datasetRef.current,
        tier: g.tier ?? "T1",
        as_user: g.as_user ?? "",
        tags: g.tags ?? [],
        holdout: g.holdout,
        authoring_status: g.authoring_status,
        golden_sql: g.golden_sql ?? "",
        golden_data: g.golden_data ?? null,
        grader: g.grader ?? {},
      });
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, []);

  // Deep-link: when a chat answer is promoted, App bumps seed.nonce — refresh
  // the list so the new draft appears, then load it into the editor.
  useEffect(() => {
    if (!seed) return;
    void refresh();
    void selectGolden(seed.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed?.nonce]);

  function newGolden() {
    setDraft(emptyDraft(dataset));
    setMsg(null);
    setSqlError(null);
  }

  function patch<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  async function runSql() {
    if (!draft.golden_sql.trim()) return;
    setBusy("sql");
    setSqlError(null);
    try {
      const res = await prepGolden({ sql: draft.golden_sql, as_user: draft.as_user || null });
      if (res.error) {
        setSqlError(res.error);
        return;
      }
      patch("golden_data", { columns: res.columns, rows: res.rows });
    } catch (e) {
      setSqlError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    if (!draft.question.trim() || !draft.golden_sql.trim()) {
      setMsg("A golden needs at least a question and SQL.");
      return;
    }
    setBusy("save");
    setMsg(null);
    try {
      const body: GoldenInput = {
        question: draft.question,
        dataset: draft.dataset,
        tier: draft.tier,
        as_user: draft.as_user || null,
        tags: draft.tags,
        holdout: draft.holdout,
        authoring_status: draft.authoring_status,
        golden_sql: draft.golden_sql,
        golden_data: draft.golden_data,
        grader: pruneGrader(draft.grader),
      };
      if (draft.id) {
        await updateGolden(draft.id, body);
      } else {
        const res = await createGolden(body);
        patch("id", res.id);
      }
      setMsg("Saved.");
      await refresh();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    if (!draft.id) return;
    setBusy("delete");
    try {
      await deleteGolden(draft.id);
      newGolden();
      await refresh();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
      <main className="admin" aria-label="Golden Examples">
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
          <h2>Golden Examples</h2>
          <Annunciators>
            <Annunciator state="on" title="Golden extracts run under RLS, same as a live ask">
              rls-scoped
            </Annunciator>
          </Annunciators>
        </div>

        <div className="admin-band" style={{ gridTemplateColumns: "260px minmax(0, 1fr)" }}>
          {/* --- Left: dataset picker + list -------------------------------- */}
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <KitSelect
              ariaLabel="Dataset"
              value={dataset}
              onValueChange={(v) => {
                setDataset(v);
                newGolden();
              }}
              options={datasets.map((d) => ({ value: d, label: d }))}
            />
            <button type="button" style={btn()} onClick={newGolden} data-testid="golden-new">
              + New
            </button>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {list.map((g) => (
                <button
                  key={g.id}
                  type="button"
                  data-testid={`golden-row-${g.id}`}
                  onClick={() => void selectGolden(g.id)}
                  style={{
                    ...btn(),
                    textAlign: "left",
                    background:
                      draft.id === g.id ? "var(--accent-soft, var(--panel-2))" : "var(--panel-2)",
                  }}
                >
                  <div style={{ fontSize: 12.5, fontWeight: 600 }}>{g.question || "(untitled)"}</div>
                  <div style={{ ...label, marginTop: 2 }}>
                    {g.tier ?? "—"} · {g.authoring_status}
                    {g.grader_kind ? ` · ${g.grader_kind}` : ""}
                  </div>
                </button>
              ))}
              {list.length === 0 && (
                <KitEmpty
                  icon={Star}
                  title="No goldens yet"
                  hint="Author one from a question, its SQL and a grader."
                />
              )}
            </div>
          </div>

          {/* --- Right: editor ------------------------------------------------ */}
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {msg && <p className="muted">{msg}</p>}

            <div style={box}>
              <label style={{ display: "block", marginBottom: 8 }}>
                <span style={label}>question</span>
                <input
                  data-testid="golden-question"
                  value={draft.question}
                  onChange={(e) => patch("question", e.target.value)}
                  style={fieldInput}
                />
              </label>
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
                <label style={{ display: "block" }}>
                  <span style={label}>dataset</span>
                  <KitSelect
                    ariaLabel="Golden dataset"
                    value={draft.dataset}
                    onValueChange={(v) => patch("dataset", v)}
                    options={datasets.map((d) => ({ value: d, label: d }))}
                  />
                </label>
                <label style={{ display: "block" }}>
                  <span style={label}>tier</span>
                  <KitSelect
                    testId="golden-tier"
                    ariaLabel="Golden tier"
                    value={draft.tier}
                    onValueChange={(v) => patch("tier", v)}
                    options={TIERS.map((t) => ({ value: t, label: t }))}
                  />
                </label>
                <label style={{ display: "block" }}>
                  <span style={label}>as_user</span>
                  <input
                    data-testid="golden-as-user"
                    value={draft.as_user}
                    placeholder="username to impersonate (RLS)"
                    onChange={(e) => patch("as_user", e.target.value)}
                    style={{ ...fieldInput, width: 180 }}
                  />
                </label>
                <label style={{ display: "block" }}>
                  <span style={label}>status</span>
                  <KitSelect
                    testId="golden-status"
                    ariaLabel="Golden status"
                    value={draft.authoring_status}
                    onValueChange={(v) => patch("authoring_status", v)}
                    options={[
                      { value: "draft", label: "draft" },
                      { value: "ready", label: "ready" },
                      { value: "archived", label: "archived" },
                    ]}
                  />
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5 }}>
                  <input
                    type="checkbox"
                    checked={draft.holdout}
                    onChange={(e) => patch("holdout", e.target.checked)}
                  />
                  holdout
                </label>
              </div>
              <div style={{ marginTop: 8 }}>
                <span style={label}>tags</span>
                <TagsEditor tags={draft.tags} onChange={(t) => patch("tags", t)} />
              </div>
            </div>

            {/* ① SQL extract */}
            <div style={box}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                <span style={{ fontSize: 13, fontWeight: 600 }}>① SQL extract</span>
                <button
                  type="button"
                  style={btn()}
                  onClick={() => patch("golden_sql", formatSql(draft.golden_sql))}
                >
                  Format
                </button>
                <button
                  type="button"
                  style={btn(!!draft.golden_sql.trim() && busy !== "sql")}
                  disabled={!draft.golden_sql.trim() || busy === "sql"}
                  onClick={() => void runSql()}
                  data-testid="golden-run-sql"
                >
                  {busy === "sql" ? "Running…" : "▶ Run SQL"}
                </button>
              </div>
              <textarea
                data-testid="golden-sql"
                value={draft.golden_sql}
                onChange={(e) => patch("golden_sql", e.target.value)}
                rows={8}
                style={{ ...mono, width: "100%", boxSizing: "border-box", padding: 8 }}
              />
              <BuilderFilter
                dataset={builderDataset}
                dims={filterDims}
                value={whereFromSql(draft.golden_sql)}
                onChange={(sql) => patch("golden_sql", setWhereClause(draft.golden_sql, sql))}
              />
              {sqlError && <p className="ex-error">{sqlError}</p>}
              {extract && (
                <div style={{ marginTop: 8 }}>
                  <SimpleTable
                    columns={extract.columns.map((c) => ({ key: c, label: c }))}
                    rows={extract.rows.map((r) =>
                      Object.fromEntries(extract.columns.map((c, i) => [c, r[i]])),
                    )}
                    max={25}
                  />
                </div>
              )}
            </div>

            {/* Grader */}
            <GraderEditor
              grader={draft.grader}
              onChange={(g) => patch("grader", g)}
              columns={graderCols}
              tier={draft.tier}
              status={draft.authoring_status}
              onStatusChange={(status) => patch("authoring_status", status)}
            />
            {graderBlocker && draft.authoring_status !== "ready" && (
              <p className="muted" style={{ fontSize: 11.5 }}>
                Not yet scoreable: {graderBlocker}
              </p>
            )}

            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <button
                type="button"
                style={btn(busy !== "save")}
                disabled={busy === "save"}
                onClick={() => void save()}
                data-testid="golden-save"
              >
                {busy === "save" ? "Saving…" : draft.id ? "Save golden" : "Create golden"}
              </button>
              {draft.id && (
                <button
                  type="button"
                  style={btn(busy !== "delete")}
                  disabled={busy === "delete"}
                  onClick={() => void remove()}
                  data-testid="golden-delete"
                >
                  {busy === "delete" ? "Deleting…" : "Delete"}
                </button>
              )}
            </div>
          </div>
        </div>
      </main>
  );
}
