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
  SkillInfo,
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

/** The augmented data the sandbox produced, as a table — the chart series the
 *  report built, i.e. how the sandbox transformed the SQL extract. */
function sandboxTable(report: unknown): { columns: string[]; rows: unknown[][] } | null {
  const chart = (report as { main_chart?: { data?: unknown } } | null)?.main_chart;
  const data = chart?.data;
  if (Array.isArray(data) && data.length > 0 && typeof data[0] === "object" && data[0] !== null) {
    const columns = Object.keys(data[0] as Record<string, unknown>);
    const rows = data.map((r) => columns.map((c) => (r as Record<string, unknown>)[c]));
    return { columns, rows };
  }
  return null;
}

/** Skill names referenced in the run_analysis code, in first-seen order. */
function appliedSkills(code: string): string[] {
  return Array.from(new Set(Array.from(code.matchAll(/skills\.(\w+)/g), (m) => m[1])));
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
  const [draftStatus, setDraftStatus] = useState<string>("");
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [selectedSkills, setSelectedSkills] = useState<Set<string>>(new Set());
  const [reasoning, setReasoning] = useState<{ skill: string; why: string }[]>([]);

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
      setDraft((d) => ({
        ...d,
        golden_sql: res.sql ?? d.golden_sql,
        golden_sandbox: res.sandbox || d.golden_sandbox,
        golden_report: report,
        golden_data: { columns: res.columns, rows: res.rows },
      }));
      if (hasPages) setReportText(JSON.stringify({ pages: res.pages }, null, 2));
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

        {/* ② Sandbox — skills */}
        <div style={box}>
          <div style={label}>② Sandbox — skills (the plan)</div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0,240px) 1fr",
              gap: 10,
              marginTop: 6,
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
                run_analysis code · add / edit / remove skill calls
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
                        onEditPages([...pagesFromReport(draft.golden_report), ...added]);
                        setMsg(
                          `Added ${added.length} page(s) from this sandbox run — scroll to the report below.`,
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
                  <div style={label}>output data · how the sandbox augmented the extract</div>
                  {(() => {
                    const t = sandboxTable(prep.report);
                    if (!t) {
                      return (
                        <div style={{ ...label, opacity: 0.6, marginTop: 4 }}>
                          no tabular output — see report JSON below
                        </div>
                      );
                    }
                    return (
                      <div style={{ overflowX: "auto", marginTop: 4 }}>
                        <table style={{ ...mono, borderCollapse: "collapse" }}>
                          <thead>
                            <tr>
                              {t.columns.map((c) => (
                                <th
                                  key={c}
                                  style={{ textAlign: "left", padding: "3px 8px", opacity: 0.7 }}
                                >
                                  {c}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {t.rows.slice(0, 12).map((row, i) => (
                              <tr key={i}>
                                {row.map((cell, j) => (
                                  <td
                                    key={j}
                                    style={{
                                      padding: "3px 8px",
                                      borderTop: "1px solid rgba(128,128,128,0.2)",
                                    }}
                                  >
                                    {String(cell)}
                                  </td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        <div style={{ ...label, marginTop: 4 }}>{t.rows.length} rows</div>
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
