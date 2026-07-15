// Golden Answer (Builder) — author eval goldens stage by stage (s14 E1).
//
// Left: per-dataset list + New. Right: the three golden stages —
//   ① SQL        (run → inspect the extract rows)
//   ② Sandbox    (run the run_analysis script → metrics/report via the agent sandbox)
//   ③ Report     (the PagesEnvelope the frontend renders — edited as JSON + preview)
// Save persists through /admin/eval-goldens; a `ready` golden is the 100/100
// benchmark the eval runner (E2) scores the agent against. A run of any upstream
// stage refreshes the extract that feeds the next — the A→B→C cascade from the plan.
import { useCallback, useEffect, useState } from "react";

import {
  GoldenInput,
  GoldenListItem,
  ObjectDigest,
  Page,
  PageObject,
  PrepResult,
  SkillInfo,
  authorObject,
  createGolden,
  deleteGolden,
  draftGoldenStream,
  getGolden,
  getGoldenSkills,
  listGoldens,
  prepGolden,
  scaffoldGolden,
  updateGolden,
} from "../../lib/api";
import { InstructResult, ReportEditor } from "./ReportEditor";

const DATASETS = ["nsw_sales", "nsw_rent"];
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
  golden_sandbox: string;
  golden_data: unknown;
  golden_report: unknown;
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
  golden_sandbox: "",
  golden_data: null,
  golden_report: null,
});

function pagesFromReport(report: unknown): Page[] {
  if (Array.isArray(report)) return report as Page[];
  const r = report as { pages?: unknown } | null;
  return r && Array.isArray(r.pages) ? (r.pages as Page[]) : [];
}

/** The augmented data the sandbox produced, as a table — the chart series the
 *  report built, i.e. how the sandbox transformed the SQL extract. House chart
 *  specs nest their rows under `main_chart.data.values` (Vega-lite shape); older
 *  or plain shapes put the array directly on `data`. Accept either. */
function sandboxTable(report: unknown): { columns: string[]; rows: unknown[][] } | null {
  const raw = (report as { main_chart?: { data?: unknown } } | null)?.main_chart?.data;
  const values = Array.isArray(raw)
    ? raw
    : Array.isArray((raw as { values?: unknown } | null)?.values)
      ? (raw as { values: unknown[] }).values
      : null;
  if (values && values.length > 0 && typeof values[0] === "object" && values[0] !== null) {
    const columns = Object.keys(values[0] as Record<string, unknown>);
    const rows = values.map((r) => columns.map((c) => (r as Record<string, unknown>)[c]));
    return { columns, rows };
  }
  return null;
}

/** Skill names referenced in the run_analysis code, in first-seen order. */
function appliedSkills(code: string): string[] {
  return Array.from(new Set(Array.from(code.matchAll(/skills\.(\w+)/g), (m) => m[1])));
}

const CHART_TYPES = new Set<string>(["trend", "breakdown", "compare"]);

/** The field mapping behind an object — what it plots / reads, as one provenance
 *  line (③ objects step): so a curator can trace enriched columns → the object. */
function objectProvenance(o: PageObject): string {
  const d = o.data as Record<string, unknown>;
  const s = (k: string) => (d[k] != null && d[k] !== "" ? String(d[k]) : null);
  if (o.type === "trend") {
    return [`x ${s("x") ?? "?"}`, `y ${s("y") ?? "?"}`, s("series") && `series ${s("series")}`]
      .filter(Boolean)
      .join(" · ");
  }
  if (o.type === "breakdown" || o.type === "compare") {
    return [
      `dim ${s("dimension") ?? "?"}`,
      `measure ${s("measure") ?? "?"}`,
      s("line_measure") && `line ${s("line_measure")}`,
      s("group") && `group ${s("group")}`,
    ]
      .filter(Boolean)
      .join(" · ");
  }
  if (o.type === "kpi") return s("basis") ? `basis ${s("basis")}` : "headline tile";
  return "";
}

/** An object's own rows as a compact table — charts carry their data inline as
 *  data.rows, so we can show the exact data behind each object. */
function objectRows(o: PageObject): { columns: string[]; rows: unknown[][] } | null {
  const raw = (o.data as { rows?: unknown }).rows;
  if (!Array.isArray(raw) || raw.length === 0) return null;
  const first = raw[0];
  if (typeof first !== "object" || first === null) return null;
  const columns = Object.keys(first as Record<string, unknown>);
  return {
    columns,
    rows: raw.map((r) => columns.map((c) => (r as Record<string, unknown>)[c])),
  };
}

/** A colour-coded object-type chip (kpi = blue, charts = green, text = grey). */
function typeChipStyle(type: string): React.CSSProperties {
  const [color, background] =
    type === "kpi"
      ? ["rgb(120,160,255)", "rgba(120,160,255,0.14)"]
      : CHART_TYPES.has(type)
        ? ["rgb(90,170,90)", "rgba(120,200,120,0.16)"]
        : ["rgb(150,150,158)", "rgba(128,128,128,0.14)"];
  return {
    fontFamily: "var(--font-mono, ui-monospace, Menlo, monospace)",
    fontSize: 10,
    textTransform: "lowercase",
    borderRadius: 5,
    padding: "1px 6px",
    fontWeight: 600,
    color,
    background,
  };
}

/** Split on a separator at paren depth 0 only (so commas inside func(a, b) or a
 *  subquery stay put) — used to lay the SELECT list out one column per line. */
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

/** Pretty-print SQL onto multiple lines: a newline before each major clause and
 *  one SELECT column per line. Purely cosmetic (whitespace + keyword case) — the
 *  text still runs exactly the same, so the extract it produces is unchanged. */
// Clause keywords that start a new line — compound/qualified forms first so the
// longest match wins (e.g. "LEFT JOIN" beats "JOIN", "UNION ALL" beats "UNION").
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
  // Break before each major clause — but ONLY at paren depth 0, so a keyword
  // inside a function or subquery (e.g. the FROM in EXTRACT(YEAR FROM month))
  // never triggers a newline.
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
  // One SELECT column per line (paren-aware) for the first SELECT clause —
  // whether it leads the string or follows a newline.
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

const box: React.CSSProperties = {
  border: "1px solid rgba(128,128,128,0.3)",
  borderRadius: 10,
  padding: "12px 14px",
};
const mono: React.CSSProperties = {
  fontFamily: "var(--font-mono, ui-monospace, Menlo, monospace)",
  fontSize: 12.5,
};
const label: React.CSSProperties = {
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: 0.5,
  opacity: 0.7,
};

function btn(active = true): React.CSSProperties {
  return {
    border: "1px solid rgba(128,128,128,0.4)",
    background: "rgba(128,128,128,0.08)",
    borderRadius: 8,
    padding: "5px 12px",
    fontSize: 13,
    cursor: active ? "pointer" : "default",
    opacity: active ? 1 : 0.5,
  };
}

/** A compact scrollable data table — reused for the SQL extract, the sandbox
 *  input (df) and the augmented output, so all three read identically. */
function DataTable({
  columns,
  rows,
  max = 12,
}: {
  columns: string[];
  rows: unknown[][];
  max?: number;
}) {
  return (
    <div style={{ overflowX: "auto", marginTop: 4 }}>
      <table style={{ ...mono, borderCollapse: "collapse" }}>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c} style={{ textAlign: "left", padding: "3px 8px", opacity: 0.7 }}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, max).map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j} style={{ padding: "3px 8px", borderTop: "1px solid rgba(128,128,128,0.2)" }}>
                  {String(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ ...label, marginTop: 4 }}>
        {rows.length} row{rows.length === 1 ? "" : "s"}
        {rows.length > max ? ` · showing ${max}` : ""}
      </div>
    </div>
  );
}

export function GoldensPage() {
  const [dataset, setDataset] = useState<string>("nsw_sales");
  const [list, setList] = useState<GoldenListItem[]>([]);
  const [draft, setDraft] = useState<Draft>(() => emptyDraft("nsw_sales"));
  const [reportText, setReportText] = useState<string>("");
  const [prep, setPrep] = useState<PrepResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [draftStatus, setDraftStatus] = useState<string>("");
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [selectedSkills, setSelectedSkills] = useState<Set<string>>(new Set());
  const [reasoning, setReasoning] = useState<{ skill: string; why: string }[]>([]);
  // The interactive report DRAFT — edits stay here until the curator Submits,
  // which commits them to golden_report + reconciles golden_data (Goal C).
  const [pendingPages, setPendingPages] = useState<Page[]>([]);
  // When an AI object-edit rewrites the ① SQL extract, this holds the previous
  // SQL so the curator can revert it in one click (s16 Q1: auto-apply + revert).
  const [sqlRevert, setSqlRevert] = useState<string | null>(null);

  /** Preselect the skills a piece of run_analysis code already applies (plus any
   *  a run reported), so the skills panel reflects what's in the code. */
  function seedSelectedSkills(code: string, used: string[] = []) {
    setSelectedSkills(new Set([...appliedSkills(code), ...used]));
  }

  // Drive both the visual editor and the raw-JSON view from the draft pages.
  function setDraftPages(next: Page[]) {
    setPendingPages(next);
    setReportText(next.length ? JSON.stringify({ pages: next }, null, 2) : "");
  }

  // The sandbox output JSON to store as golden_data — the latest good run's
  // report (or its extract rows), else whatever is already saved.
  function reconciledData(): unknown {
    if (prep && !prep.error) {
      return prep.report ?? { columns: prep.columns, rows: prep.rows };
    }
    return draft.golden_data ?? null;
  }

  const committedPages = pagesFromReport(draft.golden_report);
  const dirtyPresentation = JSON.stringify(pendingPages) !== JSON.stringify(committedPages);

  const refresh = useCallback(async () => {
    try {
      setList(await listGoldens(dataset));
    } catch (e) {
      setMsg((e as Error).message);
    }
  }, [dataset]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    getGoldenSkills()
      .then((r) => setSkills(r.skills))
      .catch(() => {});
  }, []);

  function patch<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  function insertSkill(name: string) {
    // A valid, editable call — never a bare "skills.x(" that breaks the parse.
    const line = `out = skills.${name}(df)  # edit args`;
    patch("golden_sandbox", `${draft.golden_sandbox}${draft.golden_sandbox ? "\n" : ""}${line}`);
  }

  function removeSkill(name: string) {
    const next = draft.golden_sandbox
      .split("\n")
      .filter((ln) => !ln.includes(`skills.${name}`))
      .join("\n");
    patch("golden_sandbox", next);
  }

  function toggleSkill(name: string) {
    setSelectedSkills((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  async function generateFromSkills() {
    const picked = Array.from(selectedSkills);
    if (picked.length === 0) {
      setMsg("Tick some skills first, then generate.");
      return;
    }
    setBusy("scaffold");
    setMsg(null);
    try {
      const res = await scaffoldGolden({
        question: draft.question,
        columns: prep?.columns ?? [],
        skills: picked,
      });
      if (res.code) patch("golden_sandbox", res.code);
      setReasoning(res.reasoning ?? []);
      setMsg(
        res.error
          ? `Generated with a note: ${res.error}`
          : `Regenerated run_analysis from ${picked.length} skill(s) — review & Run.`,
      );
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  function newGolden() {
    setDraft(emptyDraft(dataset));
    setReportText("");
    setPendingPages([]);
    setSelectedSkills(new Set());
    setPrep(null);
    setMsg(null);
    setSqlRevert(null);
  }

  async function selectGolden(id: string) {
    setBusy("load");
    setMsg(null);
    setPrep(null);
    setSqlRevert(null);
    try {
      const g = await getGolden(id);
      const sandbox = g.golden_sandbox ?? "";
      setDraft({
        id: g.id,
        question: g.question,
        dataset: g.dataset ?? dataset,
        tier: g.tier ?? "T1",
        as_user: g.as_user ?? "",
        tags: g.tags ?? [],
        holdout: g.holdout,
        authoring_status: g.authoring_status,
        golden_sql: formatSql(g.golden_sql ?? ""),
        golden_sandbox: sandbox,
        golden_data: g.golden_data ?? null,
        golden_report: g.golden_report ?? null,
      });
      setDraftPages(pagesFromReport(g.golden_report));
      seedSelectedSkills(sandbox);
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function runStage(withCode: boolean) {
    if (!draft.golden_sql.trim()) {
      setMsg("Add the SQL extract first.");
      return;
    }
    setBusy(withCode ? "sandbox" : "sql");
    setMsg(null);
    try {
      const res = await prepGolden({
        sql: draft.golden_sql,
        code: withCode ? draft.golden_sandbox : "",
        as_user: draft.as_user || null,
      });
      setPrep(res);
      if (res.error) {
        setMsg(res.error);
      } else if (withCode && res.report) {
        // Sandbox metrics become the golden_data that feeds the report stage.
        patch("golden_data", res.report);
        seedSelectedSkills(draft.golden_sandbox, res.skills_used);
      } else if (!withCode) {
        patch("golden_data", { columns: res.columns, rows: res.rows });
      }
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  // Edit ONE report object from a plain-English instruction (s16 full cascade):
  // the agent rebuilds the WHOLE run_analysis (every object + the change), may
  // revise the SQL extract when the data isn't present, reruns, and returns the
  // revised sql + full recomposed pages + the lifted target. On success we apply
  // every stage together — golden_sql / golden_sandbox / golden_data / the
  // presentation — so the golden stays reproducible; on failure NOTHING is
  // clobbered. Resolves with the target's type+data (+ a refresh set when the SQL
  // changed) for ReportEditor to apply, or an error string.
  async function instructObject(o: PageObject, instruction: string): Promise<InstructResult> {
    if (!draft.golden_sql.trim()) return { error: "Add the ① SQL extract first." };
    // Send every current object minus its (large) row payload so the agent knows
    // what to preserve; mark the one being edited so it changes only that.
    const digest: ObjectDigest[] = pendingPages
      .flatMap((p) => p.columns.flat())
      .map((obj) => {
        const { rows: _rows, ...fields } = obj.data as Record<string, unknown>;
        return {
          element_id: obj.element_id,
          type: obj.type,
          role: obj.role ?? null,
          data: fields,
          _target: obj.element_id === o.element_id,
        };
      });
    try {
      const res = await authorObject({
        sql: draft.golden_sql,
        code: draft.golden_sandbox,
        object_type: o.type,
        instruction,
        objects: digest,
        target_element_id: o.element_id,
        as_user: draft.as_user || null,
      });
      if (!res.object) {
        // Nothing usable was produced — leave every stored stage untouched.
        return {
          error:
            res.error ||
            "The run produced no chart — describe the measures/dimension more explicitly.",
        };
      }
      if (res.engine === "stub") {
        // The agent couldn't author the edit (no LLM key, or it hit its budget).
        // Don't apply the fallback's stand-in object or clobber any stage.
        return {
          error:
            res.error ||
            "Couldn't author this edit automatically — try rephrasing the measures/dimension.",
        };
      }
      // Success → apply the cascade in sync. Every stage the agent rebuilt lands
      // together, keeping SQL ↔ sandbox ↔ data ↔ presentation reproducible.
      const sqlChanged = !!res.sql && res.sql.trim() !== draft.golden_sql.trim();
      if (sqlChanged) {
        setSqlRevert(draft.golden_sql); // one-click revert of the previous extract
        patch("golden_sql", formatSql(res.sql as string));
      }
      if (res.code) patch("golden_sandbox", res.code);
      if (res.report) patch("golden_data", res.report);
      // Reflect the run in the ② Sandbox panel too (output table + skills used).
      setPrep({
        columns: res.columns,
        rows: res.rows,
        row_count: res.rows.length,
        report: res.report,
        pages: res.pages,
        skills_used: res.skills_used,
        skill_gaps: res.skill_gaps,
        error: res.error,
      });
      setMsg(
        `Object authored via ${res.engine} — ${sqlChanged ? "SQL + " : ""}sandbox code + ` +
          `output JSON updated${sqlChanged ? " · SQL changed (review/revert in ① SQL)" : ""}.`,
      );
      return {
        type: res.object.type,
        data: res.object.data,
        // Q2: refresh the OTHER objects' data only when the extract really changed.
        refresh: sqlChanged ? res.pages ?? undefined : undefined,
      };
    } catch (e) {
      return { error: (e as Error).message };
    }
  }

  async function save() {
    if (!draft.question.trim()) {
      setMsg("A question is required.");
      return;
    }
    // Saving commits the interactive draft: the presentation on screen is what
    // persists, and golden_data is reconciled to the latest sandbox output.
    const report: unknown = pendingPages.length ? { pages: pendingPages } : null;
    const data = reconciledData();
    setBusy("save");
    setMsg(null);
    const body: GoldenInput = {
      question: draft.question,
      dataset: draft.dataset,
      tier: draft.tier,
      as_user: draft.as_user || null,
      tags: draft.tags,
      holdout: draft.holdout,
      authoring_status: draft.authoring_status,
      golden_sql: draft.golden_sql || null,
      golden_sandbox: draft.golden_sandbox || null,
      golden_data: data ?? null,
      golden_report: report,
    };
    patch("golden_report", report);
    patch("golden_data", data ?? null);
    try {
      if (draft.id) {
        await updateGolden(draft.id, body);
        setMsg("Saved.");
      } else {
        const r = await createGolden(body);
        setDraft((d) => ({ ...d, id: r.id }));
        setMsg("Created.");
      }
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

  async function draftWithAgent() {
    if (!draft.question.trim()) {
      setMsg("Enter a question first.");
      return;
    }
    setBusy("draft");
    setMsg(null);
    setDraftStatus("starting…");
    try {
      const res = await draftGoldenStream(
        {
          question: draft.question,
          as_user: draft.as_user || null,
          dataset: draft.dataset,
        },
        (label) => setDraftStatus(label),
      );
      const hasPages = !!(res.pages && res.pages.length);
      const report = hasPages ? { pages: res.pages } : draft.golden_report;
      const sandbox = res.sandbox || draft.golden_sandbox;
      setDraft((d) => ({
        ...d,
        golden_sql: formatSql(res.sql ?? d.golden_sql),
        golden_sandbox: sandbox,
        golden_report: report,
        golden_data: { columns: res.columns, rows: res.rows },
      }));
      setDraftPages(hasPages ? (res.pages as Page[]) : pendingPages);
      seedSelectedSkills(sandbox);
      setPrep({
        columns: res.columns,
        rows: res.rows,
        row_count: res.rows.length,
        report: res.report,
        skills_used: [],
        skill_gaps: [],
        error: null,
      });
      setMsg(
        hasPages
          ? "Agent draft loaded — review & edit each stage, then save."
          : res.summary
            ? `Agent returned no chart for this question: ${res.summary.slice(0, 160)}`
            : "Agent returned no answer — try a question the dataset covers.",
      );
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  // Visual edits + the raw-JSON box both drive the DRAFT (pendingPages). Nothing
  // reaches golden_report until submitPresentation() (or Save) commits it.
  function onReportText(t: string) {
    setReportText(t);
    if (!t.trim()) {
      setPendingPages([]);
      return;
    }
    try {
      setPendingPages(pagesFromReport(JSON.parse(t)));
      setMsg(null);
    } catch {
      // keep the last valid pages on screen; Submit/Save surfaces the parse error
    }
  }

  // Submit: commit the draft presentation to golden_report AND reconcile the
  // sandbox output JSON (golden_data), so the saved golden is self-consistent.
  function submitPresentation() {
    const report = pendingPages.length ? { pages: pendingPages } : null;
    const data = reconciledData();
    patch("golden_report", report);
    patch("golden_data", data ?? null);
    setReportText(report ? JSON.stringify(report, null, 2) : "");
    setMsg(
      `Presentation submitted — golden_report (${pendingPages.length} page${
        pendingPages.length === 1 ? "" : "s"
      }) + sandbox output refreshed. Save to persist.`,
    );
  }

  return (
    <section
      style={{
        padding: 18,
        display: "grid",
        gridTemplateColumns: "260px 1fr",
        gap: 16,
        flex: 1,
        minHeight: 0,
        overflowY: "auto",
        alignContent: "start",
      }}
    >
      {/* ---- list ---- */}
      <div style={{ ...box, alignSelf: "start" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <strong>Goldens</strong>
          <button style={btn()} onClick={newGolden}>
            + New
          </button>
        </div>
        <select
          value={dataset}
          onChange={(e) => setDataset(e.target.value)}
          style={{ width: "100%", margin: "10px 0", padding: 5 }}
        >
          {DATASETS.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {list.length === 0 && (
            <span style={{ opacity: 0.6, fontSize: 13 }}>No goldens yet — author the first.</span>
          )}
          {list.map((g) => (
            <button
              key={g.id}
              onClick={() => void selectGolden(g.id)}
              style={{
                ...btn(),
                textAlign: "left",
                borderColor: draft.id === g.id ? "rgba(120,160,255,0.8)" : "rgba(128,128,128,0.4)",
              }}
            >
              <span style={{ fontSize: 13 }}>{g.question || "(untitled)"}</span>
              <span style={{ ...label, display: "block" }}>
                {g.tier} · {g.authoring_status} · {g.has_report ? "pages ✓" : "no pages"}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* ---- editor ---- */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={box}>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            <input
              value={draft.question}
              placeholder="Question, e.g. Which suburbs have the best rental yield?"
              onChange={(e) => patch("question", e.target.value)}
              style={{ flex: 1, minWidth: 260, padding: 6, fontSize: 14 }}
            />
            <select value={draft.tier} onChange={(e) => patch("tier", e.target.value)}>
              {TIERS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <select
              value={draft.authoring_status}
              onChange={(e) => patch("authoring_status", e.target.value)}
            >
              <option value="draft">draft</option>
              <option value="ready">ready</option>
            </select>
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 8 }}>
            <input
              value={draft.as_user}
              placeholder="as_user (RLS) — blank = admin"
              onChange={(e) => patch("as_user", e.target.value)}
              style={{ padding: 5, width: 220 }}
            />
            <input
              value={draft.tags.join(", ")}
              placeholder="tags, comma-separated"
              onChange={(e) =>
                patch(
                  "tags",
                  e.target.value
                    .split(",")
                    .map((t) => t.trim())
                    .filter(Boolean),
                )
              }
              style={{ padding: 5, width: 220 }}
            />
            <label style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 6 }}>
              <input
                type="checkbox"
                checked={draft.holdout}
                onChange={(e) => patch("holdout", e.target.checked)}
              />
              holdout
            </label>
          </div>
          <div
            style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}
          >
            <button
              type="button"
              style={{
                ...btn(busy !== "draft"),
                background: "rgba(120,160,255,0.18)",
                borderColor: "rgba(120,160,255,0.6)",
                fontWeight: 600,
              }}
              onClick={() => void draftWithAgent()}
              disabled={busy === "draft"}
            >
              {busy === "draft" ? "Drafting…" : "✨ Draft with agent (first pass)"}
            </button>
            <span style={busy === "draft" ? { ...label, opacity: 0.95 } : label}>
              {busy === "draft"
                ? `▷ ${draftStatus || "working…"}`
                : "runs the data-agent, then you review & edit each stage"}
            </span>
          </div>
        </div>

        {/* ① SQL */}
        <div style={box}>
          <div style={label}>① SQL — extraction</div>
          <textarea
            value={draft.golden_sql}
            onChange={(e) => {
              patch("golden_sql", e.target.value);
              setSqlRevert(null); // a manual edit supersedes the AI's rewrite
            }}
            spellCheck={false}
            rows={Math.min(16, Math.max(5, draft.golden_sql.split("\n").length + 1))}
            style={{ ...mono, width: "100%", marginTop: 6, whiteSpace: "pre", lineHeight: 1.5 }}
            placeholder={"SELECT suburb, ...\nFROM mart_rent_yield\nWHERE ..."}
          />
          {sqlRevert !== null && (
            <div
              style={{
                display: "flex",
                gap: 8,
                alignItems: "center",
                flexWrap: "wrap",
                margin: "6px 0",
                padding: "5px 10px",
                borderRadius: 6,
                fontSize: 12.5,
                border: "1px solid rgba(210,140,60,0.5)",
                background: "rgba(210,140,60,0.1)",
                color: "rgb(210,140,60)",
              }}
            >
              ● The AI object-edit rewrote this SQL to add the data it needed.
              <button
                type="button"
                style={{ ...btn(), padding: "2px 10px", fontSize: 12 }}
                onClick={() => {
                  patch("golden_sql", sqlRevert);
                  setSqlRevert(null);
                }}
              >
                ↺ revert to previous SQL
              </button>
            </div>
          )}
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button style={btn(busy !== "sql")} onClick={() => void runStage(false)} disabled={busy === "sql"}>
              {busy === "sql" ? "Running…" : "▶ Run SQL"}
            </button>
            <button
              type="button"
              style={btn(!!draft.golden_sql.trim())}
              onClick={() => patch("golden_sql", formatSql(draft.golden_sql))}
              disabled={!draft.golden_sql.trim()}
              title="pretty-print onto multiple lines (does not change what runs)"
            >
              ⋯ Format
            </button>
          </div>
          {prep && !prep.error && prep.columns.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div style={label}>extract — the exact rows the ② sandbox receives as df</div>
              <DataTable columns={prep.columns} rows={prep.rows} max={10} />
            </div>
          )}
        </div>

        {/* ② Sandbox — the data pipeline (open by default; still collapsible).
            Shows ① SQL extract → ② enrichment (derived frames) → ③ presentation
            objects, plus the editable run_analysis script + skills catalogue. */}
        <details open style={box}>
          <summary style={{ ...label, cursor: "pointer" }}>
            ② Sandbox — data pipeline · ① extract → ② enrichment → ③ objects (▶ Run skills to
            populate · click to collapse)
          </summary>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0,240px) 1fr",
              gap: 10,
              marginTop: 10,
            }}
          >
            {/* available skills — click to insert; used ones are highlighted */}
            <div style={{ minWidth: 0 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  marginBottom: 4,
                  flexWrap: "wrap",
                }}
              >
                <span style={label}>skills · ☑ select then generate · name inserts</span>
                <button
                  style={{ ...btn(busy !== "scaffold"), padding: "1px 8px", fontSize: 11 }}
                  onClick={() => void generateFromSkills()}
                  disabled={busy === "scaffold"}
                  title="the agent regenerates run_analysis code using exactly the selected skills"
                >
                  {busy === "scaffold" ? "generating…" : `⟳ generate from ${selectedSkills.size}`}
                </button>
              </div>
              <div
                style={{
                  maxHeight: 280,
                  overflowY: "auto",
                  border: "1px solid rgba(128,128,128,0.25)",
                  borderRadius: 8,
                  padding: 6,
                }}
              >
                {skills.length === 0 && <span style={{ opacity: 0.6, fontSize: 12 }}>loading…</span>}
                {skills.map((s) => {
                  const used = prep?.skills_used.includes(s.name) ?? false;
                  const picked = selectedSkills.has(s.name);
                  const why = reasoning.find((r) => r.skill === s.name)?.why;
                  return (
                    <div
                      key={s.name}
                      style={{
                        display: "flex",
                        gap: 6,
                        alignItems: "flex-start",
                        padding: "3px 6px",
                        borderRadius: 5,
                        marginBottom: 2,
                        background: used ? "rgba(120,200,120,0.18)" : "transparent",
                        borderLeft: used
                          ? "3px solid rgba(120,200,120,0.9)"
                          : "3px solid transparent",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={picked}
                        onChange={() => toggleSkill(s.name)}
                        title="select for code generation"
                        style={{ marginTop: 2 }}
                      />
                      <div
                        style={{ minWidth: 0, flex: 1, cursor: "pointer" }}
                        title={`${s.name}${s.signature}\nclick to insert`}
                        onClick={() => insertSkill(s.name)}
                      >
                        <code style={{ fontSize: 11.5 }}>{s.name}</code>
                        <span style={{ fontSize: 10, opacity: 0.5, marginLeft: 4 }}>{s.group}</span>
                        {used && (
                          <span style={{ fontSize: 10, color: "rgb(90,170,90)", marginLeft: 4 }}>
                            ✓ used
                          </span>
                        )}
                        <div
                          style={{
                            fontSize: 10.5,
                            opacity: why ? 0.85 : 0.6,
                            color: why ? "rgb(120,160,255)" : undefined,
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                          }}
                          title={why || s.doc}
                        >
                          {why ? `↳ why: ${why}` : s.doc}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* code + run + output */}
            <div style={{ minWidth: 0 }}>
              <div style={{ ...label, marginBottom: 4 }}>
                pipeline · ① SQL extract (df) → run_analysis → ② enrichment (derived frames) → ③
                presentation objects
              </div>
              {prep && !prep.error && prep.columns.length > 0 && (
                <details
                  style={{
                    marginBottom: 8,
                    padding: 6,
                    borderRadius: 6,
                    border: "1px solid rgba(128,128,128,0.25)",
                  }}
                >
                  <summary style={{ ...label, cursor: "pointer" }}>
                    ① SQL extract · df — the raw rows run_analysis receives ({prep.row_count} row
                    {prep.row_count === 1 ? "" : "s"}) · click to expand
                  </summary>
                  <div style={{ marginTop: 6 }}>
                    <DataTable columns={prep.columns} rows={prep.rows} max={6} />
                  </div>
                </details>
              )}
              <div style={{ ...label, marginBottom: 4 }}>
                run_analysis code · consumes df · add / edit / remove skill calls
              </div>
              {appliedSkills(draft.golden_sandbox).length > 0 && (
                <div
                  style={{ display: "flex", gap: 4, flexWrap: "wrap", alignItems: "center", marginBottom: 5 }}
                >
                  <span style={label}>applied to this code:</span>
                  {appliedSkills(draft.golden_sandbox).map((s) => (
                    <span
                      key={s}
                      style={{
                        ...btn(),
                        cursor: "default",
                        padding: "1px 6px",
                        fontSize: 11.5,
                        display: "inline-flex",
                        gap: 5,
                        alignItems: "center",
                      }}
                    >
                      skills.{s}
                      <span
                        onClick={() => removeSkill(s)}
                        title={`remove skills.${s} line(s)`}
                        style={{ cursor: "pointer", opacity: 0.65, fontWeight: 700 }}
                      >
                        ×
                      </span>
                    </span>
                  ))}
                </div>
              )}
              <textarea
                value={draft.golden_sandbox}
                onChange={(e) => patch("golden_sandbox", e.target.value)}
                spellCheck={false}
                rows={6}
                style={{ ...mono, width: "100%" }}
                placeholder="df is your extract. e.g.  result = skills.build_report(main_chart=skills.trend_chart(skills.trend_series(df)))"
              />
              <button
                style={btn(busy !== "sandbox")}
                onClick={() => void runStage(true)}
                disabled={busy === "sandbox"}
              >
                {busy === "sandbox" ? "Running…" : "▶ Run skills"}
              </button>
              {prep && (
                <div style={{ marginTop: 8, fontSize: 12.5 }}>
                  {/* ② enrichment — the derived frames run_analysis built and fed to a skill */}
                  <div
                    style={{
                      marginBottom: 8,
                      padding: 6,
                      borderRadius: 6,
                      border: "1px solid rgba(120,160,255,0.35)",
                      background: "rgba(120,160,255,0.05)",
                    }}
                  >
                    <div style={label}>
                      ② enrichment · derived frames run_analysis built (avg price, growth, moving
                      avgs…) — the data behind the objects
                    </div>
                    {prep.frames && prep.frames.length > 0 ? (
                      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 6 }}>
                        {prep.frames.map((f) => (
                          <div key={f.name} style={{ minWidth: 0 }}>
                            <div
                              style={{ display: "flex", gap: 6, alignItems: "baseline", flexWrap: "wrap" }}
                            >
                              <code style={{ fontSize: 12, color: "rgb(120,160,255)" }}>{f.name}</code>
                              <span style={{ ...label, opacity: 0.55 }}>
                                {f.shape[0]} × {f.shape[1]}
                              </span>
                              <span style={{ ...label, opacity: 0.9, color: f.fed_object ? "rgb(90,170,90)" : undefined }}>
                                {f.fed_object ? "→ chart" : "→ kpi / derived"}
                              </span>
                            </div>
                            <DataTable columns={f.columns} rows={f.rows} max={8} />
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div style={{ ...label, opacity: 0.6, marginTop: 4 }}>
                        no named derived frames captured — the run computed inline, or fed the extract
                        straight to a skill
                      </div>
                    )}
                  </div>
                  {/* ③ presentation objects — what the report renders */}
                  <div
                    style={{
                      marginBottom: 8,
                      padding: 6,
                      borderRadius: 6,
                      border: "1px solid rgba(120,200,120,0.4)",
                      background: "rgba(120,200,120,0.06)",
                    }}
                  >
                    <div style={label}>
                      ③ presentation objects · the report this run built (feeds the ③ Report below)
                    </div>
                    <div style={{ ...label, opacity: 0.7 }}>
                      each object · the data + fields behind it
                    </div>
                    {(() => {
                      const objs = (prep.pages ?? []).flatMap((p) => (p.columns ?? []).flat());
                      if (objs.length === 0) {
                        // No composed pages this run — fall back to the main chart's data.
                        const t = sandboxTable(prep.report);
                        return t ? (
                          <DataTable columns={t.columns} rows={t.rows} max={12} />
                        ) : (
                          <div style={{ ...label, opacity: 0.6, marginTop: 4 }}>
                            no tabular output — see report JSON below
                          </div>
                        );
                      }
                      return (
                        <div
                          style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 6 }}
                        >
                          {objs.map((o, i) => {
                            const d = o.data as Record<string, unknown>;
                            const title = String(d.title ?? d.label ?? d.heading ?? o.type);
                            const prov = objectProvenance(o);
                            const t = objectRows(o);
                            return (
                              <div
                                key={o.element_id ?? i}
                                style={{
                                  border: "1px solid rgba(120,200,120,0.35)",
                                  borderRadius: 8,
                                  padding: "8px 10px",
                                  background: "rgba(128,128,128,0.04)",
                                }}
                              >
                                <div
                                  style={{
                                    display: "flex",
                                    gap: 7,
                                    alignItems: "baseline",
                                    flexWrap: "wrap",
                                  }}
                                >
                                  <span style={typeChipStyle(o.type)}>{o.type}</span>
                                  <span
                                    style={{
                                      fontSize: 12.5,
                                      fontWeight: 600,
                                      overflow: "hidden",
                                      textOverflow: "ellipsis",
                                      whiteSpace: "nowrap",
                                    }}
                                  >
                                    {title}
                                  </span>
                                </div>
                                {prov && (
                                  <div
                                    style={{
                                      fontSize: 11,
                                      opacity: 0.75,
                                      marginTop: 3,
                                      fontFamily: "var(--font-mono, ui-monospace, monospace)",
                                    }}
                                  >
                                    ← {prov}
                                  </div>
                                )}
                                {o.type === "kpi" ? (
                                  <div
                                    style={{
                                      marginTop: 6,
                                      display: "inline-flex",
                                      flexDirection: "column",
                                      border: "1px solid rgba(128,128,128,0.25)",
                                      borderRadius: 8,
                                      padding: "6px 12px",
                                      minWidth: 120,
                                    }}
                                  >
                                    <span style={{ fontSize: 16, fontWeight: 700, letterSpacing: -0.3 }}>
                                      {String(d.value ?? "—")}
                                    </span>
                                    <span style={{ ...label, opacity: 0.6 }}>{String(d.label ?? "")}</span>
                                  </div>
                                ) : t ? (
                                  <div style={{ marginTop: 4 }}>
                                    <DataTable columns={t.columns} rows={t.rows} max={6} />
                                  </div>
                                ) : o.type === "text" || o.type === "insight" ? (
                                  <div
                                    style={{
                                      fontSize: 12,
                                      opacity: 0.85,
                                      marginTop: 4,
                                      whiteSpace: "pre-wrap",
                                    }}
                                  >
                                    {String(d.text ?? d.heading ?? "")}
                                  </div>
                                ) : (
                                  <div style={{ ...label, opacity: 0.5, marginTop: 4 }}>
                                    no rows captured
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      );
                    })()}
                  </div>
                  <div style={label}>skills used</div>
                  <div style={{ display: "flex", gap: 4, flexWrap: "wrap", margin: "3px 0 6px" }}>
                    {prep.skills_used.length ? (
                      prep.skills_used.map((s) => (
                        <span
                          key={s}
                          style={{
                            ...btn(),
                            cursor: "default",
                            padding: "1px 7px",
                            background: "rgba(120,200,120,0.18)",
                          }}
                        >
                          {s}
                        </span>
                      ))
                    ) : (
                      <span style={{ opacity: 0.6 }}>— none —</span>
                    )}
                  </div>
                  {prep.pages && prep.pages.length > 0 && (
                    <button
                      style={{ ...btn(), marginBottom: 6 }}
                      onClick={() => {
                        const added = prep.pages ?? [];
                        setDraftPages([...pendingPages, ...added]);
                        setMsg(
                          `Added ${added.length} page(s) from this sandbox run to the draft — scroll to ③ Report, then Submit.`,
                        );
                      }}
                    >
                      ＋ Add this output as report page(s)
                    </button>
                  )}
                  {prep.error && (
                    <div style={{ color: "#c0392b", marginBottom: 6, whiteSpace: "pre-wrap" }}>
                      error: {prep.error}
                    </div>
                  )}
                  {prep.skill_gaps.length > 0 && (
                    <div style={{ ...label, marginBottom: 4 }}>
                      skill gaps: {prep.skill_gaps.map((g) => g.need).join(", ")}
                    </div>
                  )}
                  {(() => {
                    const r = prep.report as {
                      summary?: string;
                      headlines?: { label?: string; value?: string }[];
                    } | null;
                    if (!r || (!r.summary && !(r.headlines && r.headlines.length))) return null;
                    return (
                      <div style={{ marginBottom: 8 }}>
                        {r.summary && (
                          <div style={{ fontSize: 12.5, marginBottom: 4, whiteSpace: "pre-wrap" }}>
                            {r.summary}
                          </div>
                        )}
                        {Array.isArray(r.headlines) && r.headlines.length > 0 && (
                          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                            {r.headlines.map((h, i) => (
                              <span key={i} style={{ ...btn(), cursor: "default", padding: "2px 8px" }}>
                                <strong>{h.value}</strong>{" "}
                                <span style={{ opacity: 0.7 }}>{h.label}</span>
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })()}
                  <details style={{ marginTop: 6 }}>
                    <summary style={{ cursor: "pointer", fontSize: 11, opacity: 0.7 }}>
                      report JSON
                    </summary>
                    <pre
                      style={{
                        ...mono,
                        overflowX: "auto",
                        maxHeight: 200,
                        background: "rgba(128,128,128,0.08)",
                        padding: 8,
                        borderRadius: 6,
                      }}
                    >
                      {JSON.stringify(
                        prep.report ?? { columns: prep.columns, rows: prep.rows.slice(0, 12) },
                        null,
                        2,
                      )}
                    </pre>
                  </details>
                </div>
              )}
            </div>
          </div>
        </details>

        {/* ③ Report */}
        <div style={box}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <div style={label}>③ Report — presentation (interactive draft)</div>
            {(() => {
              const objs = pendingPages.reduce(
                (n, p) => n + (p.columns?.reduce((m, c) => m + c.length, 0) ?? 0),
                0,
              );
              return (
                <span style={{ ...label, opacity: 0.6 }}>
                  {pendingPages.length} page(s) · {objs} object(s)
                </span>
              );
            })()}
            {dirtyPresentation && (
              <span style={{ ...label, color: "rgb(210,140,60)", opacity: 0.95 }}>
                ● unsubmitted edits
              </span>
            )}
            <button
              type="button"
              style={{
                ...btn(),
                marginLeft: "auto",
                fontWeight: 600,
                background: dirtyPresentation ? "rgba(120,160,255,0.22)" : "rgba(128,128,128,0.08)",
                borderColor: dirtyPresentation ? "rgba(120,160,255,0.6)" : "rgba(128,128,128,0.4)",
              }}
              onClick={submitPresentation}
              title="commit these edits to golden_report + refresh the sandbox output JSON (golden_data)"
            >
              ⤴ Submit presentation
            </button>
          </div>
          <div style={{ ...label, opacity: 0.5, marginTop: 3 }}>
            move objects across columns · add / edit / remove objects & pages · then Submit to refresh
            golden_report + sandbox output JSON (the golden answer)
          </div>
          <div style={{ marginTop: 8 }}>
            <ReportEditor
              pages={pendingPages}
              onChange={setDraftPages}
              onInstruct={instructObject}
            />
          </div>
          <details style={{ marginTop: 10 }}>
            <summary style={{ cursor: "pointer", fontSize: 12, opacity: 0.7 }}>
              raw golden_report JSON (draft — edit here or above; Submit / Save commits it)
            </summary>
            <textarea
              value={reportText}
              onChange={(e) => onReportText(e.target.value)}
              spellCheck={false}
              rows={8}
              style={{ ...mono, width: "100%", marginTop: 6 }}
              placeholder='{ "pages": [ { "template": "two-col", "columns": [[], []] } ] }'
            />
          </details>
          <details style={{ marginTop: 6 }}>
            <summary style={{ cursor: "pointer", fontSize: 12, opacity: 0.7 }}>
              sandbox output JSON (golden_data — reconciled on Submit / Save)
            </summary>
            <pre
              style={{
                ...mono,
                overflowX: "auto",
                maxHeight: 220,
                background: "rgba(128,128,128,0.08)",
                padding: 8,
                borderRadius: 6,
                marginTop: 6,
              }}
            >
              {draft.golden_data
                ? JSON.stringify(draft.golden_data, null, 2)
                : "— run ② Sandbox, then Submit —"}
            </pre>
          </details>
        </div>

        {/* actions */}
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <button style={btn(busy !== "save")} onClick={() => void save()} disabled={busy === "save"}>
            {draft.id ? "Save golden" : "Create golden"}
          </button>
          {draft.id && (
            <button style={{ ...btn(), color: "#c0392b" }} onClick={() => void remove()}>
              Delete
            </button>
          )}
          {msg && <span style={{ fontSize: 13, opacity: 0.85 }}>{msg}</span>}
        </div>
      </div>
    </section>
  );
}
