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
  Page,
  PrepResult,
  createGolden,
  deleteGolden,
  draftGolden,
  getGolden,
  listGoldens,
  prepGolden,
  updateGolden,
} from "../../lib/api";
import { ReportEditor } from "./ReportEditor";

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

export function GoldensPage() {
  const [dataset, setDataset] = useState<string>("nsw_sales");
  const [list, setList] = useState<GoldenListItem[]>([]);
  const [draft, setDraft] = useState<Draft>(() => emptyDraft("nsw_sales"));
  const [reportText, setReportText] = useState<string>("");
  const [prep, setPrep] = useState<PrepResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

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

  function patch<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  function newGolden() {
    setDraft(emptyDraft(dataset));
    setReportText("");
    setPrep(null);
    setMsg(null);
  }

  async function selectGolden(id: string) {
    setBusy("load");
    setMsg(null);
    setPrep(null);
    try {
      const g = await getGolden(id);
      setDraft({
        id: g.id,
        question: g.question,
        dataset: g.dataset ?? dataset,
        tier: g.tier ?? "T1",
        as_user: g.as_user ?? "",
        tags: g.tags ?? [],
        holdout: g.holdout,
        authoring_status: g.authoring_status,
        golden_sql: g.golden_sql ?? "",
        golden_sandbox: g.golden_sandbox ?? "",
        golden_data: g.golden_data ?? null,
        golden_report: g.golden_report ?? null,
      });
      setReportText(g.golden_report ? JSON.stringify(g.golden_report, null, 2) : "");
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
      } else if (!withCode) {
        patch("golden_data", { columns: res.columns, rows: res.rows });
      }
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    if (!draft.question.trim()) {
      setMsg("A question is required.");
      return;
    }
    let report: unknown = null;
    if (reportText.trim()) {
      try {
        report = JSON.parse(reportText);
      } catch {
        setMsg("The report is not valid JSON — fix it before saving.");
        return;
      }
    }
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
      golden_data: draft.golden_data ?? null,
      golden_report: report,
    };
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
    try {
      const res = await draftGolden({
        question: draft.question,
        as_user: draft.as_user || null,
        dataset: draft.dataset,
      });
      const report = res.pages ? { pages: res.pages } : draft.golden_report;
      setDraft((d) => ({ ...d, golden_sql: res.sql ?? d.golden_sql, golden_report: report }));
      if (res.pages) setReportText(JSON.stringify({ pages: res.pages }, null, 2));
      setPrep({
        columns: res.columns,
        rows: res.rows,
        row_count: res.rows.length,
        report: res.report,
        skills_used: [],
        skill_gaps: [],
        error: null,
      });
      setMsg("Agent draft loaded — review and edit each stage, then save.");
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  // Visual edits from ReportEditor and the raw JSON box both drive golden_report.
  function onEditPages(next: Page[]) {
    const report = { pages: next };
    patch("golden_report", report);
    setReportText(JSON.stringify(report, null, 2));
  }
  function onReportText(t: string) {
    setReportText(t);
    if (!t.trim()) {
      patch("golden_report", null);
      return;
    }
    try {
      patch("golden_report", JSON.parse(t));
      setMsg(null);
    } catch {
      // keep the last valid pages on screen; save() surfaces the parse error
    }
  }

  return (
    <section style={{ padding: 18, display: "grid", gridTemplateColumns: "260px 1fr", gap: 16 }}>
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
            <span style={label}>runs the data-agent, then you review &amp; edit each stage</span>
          </div>
        </div>

        {/* ① SQL */}
        <div style={box}>
          <div style={label}>① SQL — extraction</div>
          <textarea
            value={draft.golden_sql}
            onChange={(e) => patch("golden_sql", e.target.value)}
            spellCheck={false}
            rows={5}
            style={{ ...mono, width: "100%", marginTop: 6 }}
            placeholder="SELECT suburb, ... FROM mart_rent_yield WHERE ..."
          />
          <button style={btn(busy !== "sql")} onClick={() => void runStage(false)} disabled={busy === "sql"}>
            {busy === "sql" ? "Running…" : "▶ Run SQL"}
          </button>
          {prep && !prep.error && prep.columns.length > 0 && (
            <div style={{ overflowX: "auto", marginTop: 8 }}>
              <table style={{ ...mono, borderCollapse: "collapse" }}>
                <thead>
                  <tr>
                    {prep.columns.map((c) => (
                      <th key={c} style={{ textAlign: "left", padding: "3px 8px", opacity: 0.7 }}>
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {prep.rows.slice(0, 10).map((row, i) => (
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
              <div style={{ ...label, marginTop: 4 }}>{prep.row_count} rows</div>
            </div>
          )}
        </div>

        {/* ② Sandbox */}
        <div style={box}>
          <div style={label}>② Sandbox — preparation (the plan)</div>
          <textarea
            value={draft.golden_sandbox}
            onChange={(e) => patch("golden_sandbox", e.target.value)}
            spellCheck={false}
            rows={4}
            style={{ ...mono, width: "100%", marginTop: 6 }}
            placeholder="# run_analysis over `df` via skills.*  →  result = skills.build_report(...)"
          />
          <button
            style={btn(busy !== "sandbox")}
            onClick={() => void runStage(true)}
            disabled={busy === "sandbox"}
          >
            {busy === "sandbox" ? "Running…" : "▶ Run prep (sandbox)"}
          </button>
          {prep && (prep.report || prep.skills_used.length > 0) && (
            <div style={{ marginTop: 8, fontSize: 13 }}>
              <div style={label}>skills used: {prep.skills_used.join(", ") || "—"}</div>
              <pre style={{ ...mono, overflowX: "auto", maxHeight: 180 }}>
                {JSON.stringify(prep.report, null, 2)}
              </pre>
            </div>
          )}
        </div>

        {/* ③ Report */}
        <div style={box}>
          <div style={label}>③ Report — presentation (interactive)</div>
          <div style={{ marginTop: 8 }}>
            <ReportEditor pages={pagesFromReport(draft.golden_report)} onChange={onEditPages} />
          </div>
          <details style={{ marginTop: 10 }}>
            <summary style={{ cursor: "pointer", fontSize: 12, opacity: 0.7 }}>raw JSON</summary>
            <textarea
              value={reportText}
              onChange={(e) => onReportText(e.target.value)}
              spellCheck={false}
              rows={8}
              style={{ ...mono, width: "100%", marginTop: 6 }}
              placeholder='{ "pages": [ { "template": "summary", "columns": [] } ] }'
            />
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
