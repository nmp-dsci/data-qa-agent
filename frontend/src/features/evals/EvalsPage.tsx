// Evaluations tab (s24 M4) — the read surface over scored eval runs.
//
// Left: every run, newest first, so quality over time is visible rather than
// anecdotal. Right: one run — its build fingerprint, pillar scores, the
// regression gate against its baseline when it is an experiment, and per-case
// results each linking to the query_runs trace that produced it.
//
// Deliberately read-only. Runs come from `make eval`; nothing here can produce
// a score, so the dashboard can never disagree with the recorded evidence.

import { Fragment, useCallback, useEffect, useState } from "react";
import {
  EvalAnalysisPass,
  EvalCaseResult,
  EvalRun,
  EvalRunDetail,
  getEvalRun,
  getEvalRuns,
} from "../../lib/api";
import { Annunciator, HudBox } from "../../ui/flightdeck";

const card: React.CSSProperties = {
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 10,
  padding: "12px 14px",
};

const label: React.CSSProperties = {
  fontSize: 10.5,
  textTransform: "uppercase",
  letterSpacing: 0.8,
  color: "var(--faint)",
  fontWeight: 600,
};

const mono = "var(--mono, ui-monospace, SFMono-Regular, Menlo, monospace)";

function fmt(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? "—" : Number(value).toFixed(digits);
}

function fmtInt(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : Number(value).toLocaleString();
}

function fmtCost(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `$${Number(value).toFixed(4)}`;
}

function fmtSecs(ms: number | null | undefined): string {
  return ms === null || ms === undefined ? "—" : `${(Number(ms) / 1000).toFixed(1)}s`;
}

/** MLflow's UI: same host as the app, its own port (MLFLOW_HOST_PORT, 5500 by
 *  default). The frontend has no config for it, so derive from the location —
 *  a link that lands on the local stack is worth more than none. */
function mlflowBase(): string {
  const host = typeof window === "undefined" ? "localhost" : window.location.hostname;
  return `http://${host}:5500`;
}

/** A run's headline identity: experiment label if it has one, else "baseline". */
function runTitle(run: EvalRun): string {
  return run.experiment_id ? run.experiment_id : "baseline";
}

/** Pillar scores are literally instrument readouts, so they get the HUD box
 *  (s25) rather than a plain card — corner ticks, mono-caps caption, tabular
 *  value. */
function Pillar({ name, value, hint }: { name: string; value: string; hint?: string }) {
  return (
    <HudBox label={name} value={value}>
      {hint && <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 2 }}>{hint}</div>}
    </HudBox>
  );
}

function GateBadge({ gate, comparable }: { gate: string; comparable: boolean }) {
  return (
    <Annunciator state={gate === "PASS" ? "on" : "bad"}>
      gate {gate}
      {!comparable && " · packs differ"}
    </Annunciator>
  );
}

/** The judge's verdict (s49 M2): a label, and — when it isn't "high" — the stage
 *  it blames. Rendered next to PASS/FAIL but deliberately not styled like it:
 *  the label does NOT gate (decision D2), and a reader who confuses the two will
 *  chase a "failure" the gate never called one. */
function JudgeBadge({ judge }: { judge: EvalCaseResult["judge"] }) {
  const label = judge?.label;
  if (!label) {
    return (
      <span style={{ fontSize: 11.5, color: "var(--faint)" }} title={judge?.reason ?? "no judge"}>
        —
      </span>
    );
  }
  const tone =
    label === "high" ? "var(--good)" : label === "medium" ? "var(--warn)" : "var(--bad)";
  return (
    <span title={judge?.reason ?? ""}>
      <span
        style={{
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: 0.6,
          textTransform: "uppercase",
          color: tone,
          border: `1px solid ${tone}`,
          borderRadius: 6,
          padding: "1px 6px",
        }}
      >
        {label}
      </span>
      {judge?.diagnosis && judge.diagnosis !== "none" && (
        <span style={{ fontSize: 11, color: "var(--faint)", marginLeft: 6 }}>
          {judge.diagnosis}
        </span>
      )}
      {judge?.calibrated === false && (
        <span style={{ fontSize: 10, color: "var(--warn)", marginLeft: 6 }} title="the judge did not reproduce known labels on this run">
          uncal
        </span>
      )}
    </span>
  );
}

/** Checkpoint scores (s49 D1) — where it went wrong, never whether. Shown as a
 *  muted one-liner under the case so it reads as diagnosis, not verdict. */
function Checkpoints({ checkpoints }: { checkpoints: EvalCaseResult["checkpoints"] }) {
  const parts = (["sql", "analysis", "deck"] as const)
    .map((stage) => {
      const score = checkpoints?.[stage]?.score;
      return score === null || score === undefined ? null : `${stage} ${score.toFixed(2)}`;
    })
    .filter(Boolean);
  if (!parts.length) return null;
  return (
    <div style={{ fontFamily: mono, fontSize: 10.5, color: "var(--faint)", marginTop: 2 }}>
      checkpoints · {parts.join(" · ")}
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * s50 · case drill-down. The table answers "did it pass"; this answers "what
 * did the agent actually say, run and spend, against what the golden asked
 * for" — the depth an investigation needs, one click from the verdict.
 * ------------------------------------------------------------------------- */

const sectionTitle: React.CSSProperties = {
  fontSize: 12.5,
  fontWeight: 600,
  color: "rgb(120,160,255)",
  letterSpacing: 0.4,
};

const block: React.CSSProperties = {
  ...card,
  marginTop: 10,
};

const twoCol: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)",
  gap: 12,
  marginTop: 8,
};

const preStyle: React.CSSProperties = {
  margin: 0,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  maxHeight: 320,
  overflow: "auto",
  fontSize: 11.5,
};

const proseStyle: React.CSSProperties = {
  whiteSpace: "pre-wrap",
  fontSize: 13,
  lineHeight: 1.5,
  color: "var(--text)",
  margin: 0,
};

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={block}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <span style={sectionTitle}>◆ {title}</span>
        {hint && <span style={label}>{hint}</span>}
      </div>
      {children}
    </div>
  );
}

function Dash({ value }: { value: React.ReactNode }) {
  return value === null || value === undefined || value === "" ? (
    <span style={{ color: "var(--faint)" }}>—</span>
  ) : (
    <>{value}</>
  );
}

/** Code with the page's `.sql` look; collapsed behind <details> when asked,
 *  because a sandbox pass's code or stdout is evidence, not the headline. */
function Code({
  text,
  collapsed,
  summary,
}: {
  text: string | null | undefined;
  collapsed?: boolean;
  summary?: string;
}) {
  if (!text) return <Dash value={null} />;
  const pre = (
    <pre className="sql" style={preStyle}>
      {text}
    </pre>
  );
  if (!collapsed) return pre;
  return (
    <details style={{ marginTop: 4 }}>
      <summary style={{ ...label, cursor: "pointer" }}>{summary ?? "show"}</summary>
      {pre}
    </details>
  );
}

function Chips({ items, missing }: { items?: string[] | null; missing?: string[] | null }) {
  if (!items || items.length === 0) return <Dash value={null} />;
  const miss = new Set(missing ?? []);
  return (
    <span style={{ display: "inline-flex", flexWrap: "wrap", gap: 4 }}>
      {items.map((it) => (
        <span
          key={it}
          style={{
            fontFamily: mono,
            fontSize: 10.5,
            padding: "1px 6px",
            borderRadius: 6,
            border: `1px solid ${miss.has(it) ? "var(--warn-border)" : "var(--border)"}`,
            color: miss.has(it) ? "var(--warn)" : "var(--muted)",
          }}
        >
          {it}
        </span>
      ))}
    </span>
  );
}

function KV({ k, v, warn }: { k: string; v: React.ReactNode; warn?: boolean }) {
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "baseline", marginTop: 3 }}>
      <span style={{ ...label, minWidth: 96, flex: "none" }}>{k}</span>
      <span style={{ fontSize: 12, color: warn ? "var(--warn)" : "var(--text)", minWidth: 0 }}>
        <Dash value={v} />
      </span>
    </div>
  );
}

function ScoreBadge({ score }: { score: number | null | undefined }) {
  if (score === null || score === undefined) return <Annunciator state="off">—</Annunciator>;
  const state = score >= 0.8 ? "on" : score >= 0.5 ? "warn" : "bad";
  return <Annunciator state={state}>{score.toFixed(2)}</Annunciator>;
}

/** G1 records whatever its grader kind returns beyond kind/score — the
 *  expected/actual values, tolerance, matched keys. Render what is there. */
function G1Fields({ g1 }: { g1: Record<string, unknown> }) {
  const entries = Object.entries(g1).filter(([k]) => !["kind", "score"].includes(k));
  if (entries.length === 0) return null;
  return (
    <div style={{ marginTop: 8, borderTop: "1px solid var(--border)", paddingTop: 6 }}>
      <span style={label}>G1 · {String(g1.kind ?? "grader")} fields</span>
      {entries.map(([k, v]) => (
        <KV
          key={k}
          k={k}
          v={
            typeof v === "object" && v !== null ? (
              <code style={{ fontSize: 11 }}>{JSON.stringify(v)}</code>
            ) : (
              String(v)
            )
          }
          warn={k === "error"}
        />
      ))}
    </div>
  );
}

function PassView({ pass, index }: { pass: EvalAnalysisPass; index: number }) {
  const bad = pass.status !== "ok";
  return (
    <div style={{ borderTop: index > 0 ? "1px solid var(--border)" : undefined, paddingTop: index > 0 ? 8 : 0, marginTop: index > 0 ? 8 : 0 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ ...label, color: "var(--muted)" }}>pass {index + 1}</span>
        <Annunciator state={bad ? "bad" : "on"}>{pass.status ?? "—"}</Annunciator>
        <span style={{ fontFamily: mono, fontSize: 11, color: "var(--faint)" }}>
          {pass.runtime ?? "—"} · {pass.ms === null ? "—" : `${pass.ms} ms`}
          {pass.code_sha && ` · ${pass.code_sha}`}
        </span>
        <Chips items={pass.skills_used} />
      </div>
      <Code text={pass.code} collapsed summary="code" />
      {pass.stdout && <Code text={pass.stdout} collapsed summary="stdout" />}
      {pass.error && (
        <pre className="sql" style={{ ...preStyle, color: "var(--bad)", marginTop: 4 }}>
          {pass.error}
        </pre>
      )}
    </div>
  );
}

function CaseDrilldown({ result, run }: { result: EvalCaseResult; run: EvalRun }) {
  const judge = result.judge ?? {};
  const g1 = (result.g1 ?? {}) as Record<string, unknown>;
  const cp = result.checkpoints ?? {};
  const gcp = result.golden_checkpoints ?? {};
  const trace = result.trace ?? { sql: [], analysis: null };
  const manifest = result.artifact_manifest ?? null;
  const slides = manifest?.slides ?? [];
  const deckUrl = result.artifact_deck_url ?? manifest?.deck_url ?? null;
  const sheetUrl = result.artifact_sheet_url ?? manifest?.sheet_url ?? null;
  const graderKind = String((result.grader ?? {}).kind ?? g1.kind ?? "");
  const showValues = ["scalar", "ranked_set", "series"].includes(graderKind);
  const primarySql = result.sql_text ?? null;
  const otherSql = trace.sql.filter((s) => (s.sql ?? "").trim() !== (primarySql ?? "").trim());
  const base = mlflowBase();
  const traceExp = run.mlflow_experiment_id ?? "1";

  return (
    <div style={{ padding: "4px 0 14px", minWidth: 0, overflowWrap: "anywhere" }} data-testid="eval-case-drilldown">
      {/* 1 · verdict strip */}
      <Section title="VERDICT">
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginTop: 8 }}>
          <Annunciator state={result.passed ? "on" : "bad"}>{result.passed ? "PASS" : "FAIL"}</Annunciator>
          <span style={{ fontFamily: mono, fontSize: 11.5 }}>
            G1 {fmt(result.g1?.score)}
            {g1.kind ? ` · ${String(g1.kind)}` : ""}
            {g1.scalar_source ? ` · ${String(g1.scalar_source)}` : ""}
          </span>
          <span style={{ fontFamily: mono, fontSize: 11.5 }}>
            G5 {result.g5?.passed === undefined || result.g5?.passed === null ? "—" : result.g5.passed ? "passed" : "failed"}
          </span>
          <JudgeBadge judge={judge} />
          {result.degraded && <Annunciator state="warn">degraded</Annunciator>}
        </div>
        {judge.reason && (
          <p style={{ fontSize: 12.5, color: "var(--muted)", margin: "8px 0 0" }}>
            <span style={label}>judge · </span>
            {judge.reason}
          </p>
        )}
        <div style={{ fontFamily: mono, fontSize: 10.5, color: "var(--faint)", marginTop: 4 }}>
          {judge.model ?? "no judge"}
          {judge.calibrated !== undefined && ` · ${judge.calibrated ? "calibrated" : "NOT calibrated"}`}
          {judge.effort && ` · ${judge.effort}`}
        </div>
      </Section>

      {/* 2 · answer vs golden */}
      <Section title="ANSWER vs GOLDEN" hint="what the judge read, and what it graded against">
        <div style={twoCol}>
          <div>
            <span style={label}>agent answer</span>
            <p style={proseStyle}>
              <Dash value={result.answer} />
            </p>
          </div>
          <div>
            <span style={label}>golden answer · label {result.label ?? "—"}</span>
            <p style={proseStyle}>
              <Dash value={result.golden_answer} />
            </p>
            {result.expectation && (
              <p style={{ ...proseStyle, color: "var(--muted)", fontSize: 12, marginTop: 6 }}>
                {result.expectation}
              </p>
            )}
          </div>
        </div>
        {showValues && <G1Fields g1={g1} />}
      </Section>

      {/* 3 · checkpoints side by side */}
      <Section title="CHECKPOINTS" hint="expected (golden) vs actual (this run) · diagnostic, never gates">
        <div style={{ display: "grid", gridTemplateColumns: "72px 90px minmax(0,1fr) minmax(0,1fr)", gap: "6px 12px", marginTop: 8, alignItems: "start" }}>
          <span style={label}>score</span>
          <span style={label}>stage</span>
          <span style={label}>expected</span>
          <span style={label}>actual</span>

          <ScoreBadge score={cp.sql?.score} />
          <span style={{ fontFamily: mono, fontSize: 11.5 }}>SQL</span>
          <div>
            <KV k="key_cols" v={<Chips items={gcp.sql?.key_cols} />} />
          </div>
          <div>
            <KV k="rows_match" v={cp.sql?.rows_match === undefined ? null : fmt(cp.sql.rows_match)} warn={(cp.sql?.score ?? 1) < 0.8} />
            <KV k="keys" v={cp.sql?.golden_keys === undefined && cp.sql?.actual_keys === undefined ? null : `golden ${cp.sql?.golden_keys ?? "—"} · actual ${cp.sql?.actual_keys ?? "—"} · matched ${cp.sql?.matched ?? "—"}`} />
            {(cp.sql?.missing?.length ?? 0) > 0 && <KV k="missing" v={<Chips items={cp.sql?.missing} missing={cp.sql?.missing} />} warn />}
          </div>

          <ScoreBadge score={cp.analysis?.score} />
          <span style={{ fontFamily: mono, fontSize: 11.5 }}>ANALYSIS</span>
          <div>
            <KV k="skills" v={<Chips items={gcp.analysis?.expected_skills} missing={cp.analysis?.missing_skills} />} />
            <KV k="derived_cols" v={<Chips items={gcp.analysis?.derived_cols} missing={cp.analysis?.missing_cols} />} />
          </div>
          <div>
            <KV k="skills_used" v={<Chips items={cp.analysis?.skills_used} />} />
            <KV k="missing" v={(cp.analysis?.missing_skills?.length ?? 0) + (cp.analysis?.missing_cols?.length ?? 0) === 0 ? "none" : <Chips items={[...(cp.analysis?.missing_skills ?? []), ...(cp.analysis?.missing_cols ?? [])]} missing={[...(cp.analysis?.missing_skills ?? []), ...(cp.analysis?.missing_cols ?? [])]} />} warn={(cp.analysis?.missing_skills?.length ?? 0) + (cp.analysis?.missing_cols?.length ?? 0) > 0} />
          </div>

          <ScoreBadge score={cp.deck?.score} />
          <span style={{ fontFamily: mono, fontSize: 11.5 }}>DECK</span>
          <div>
            <KV k="layouts" v={<Chips items={gcp.deck?.layouts_any_of} />} />
            <KV k="kpi label ∋" v={gcp.deck?.kpi_label_contains} />
          </div>
          <div>
            <KV k="layouts_used" v={<Chips items={cp.deck?.layouts_used} />} warn={cp.deck?.layout_hit === false} />
            <KV k="kpi_labels" v={cp.deck?.kpi_labels?.length ? cp.deck.kpi_labels.join(" · ") : null} warn={cp.deck?.kpi_hit === false} />
            <KV k="hits" v={cp.deck?.layout_hit === undefined && cp.deck?.kpi_hit === undefined ? null : `layout ${cp.deck?.layout_hit ? "✓" : "✗"} · kpi ${cp.deck?.kpi_hit ? "✓" : "✗"}`} warn={cp.deck?.layout_hit === false || cp.deck?.kpi_hit === false} />
          </div>
        </div>
      </Section>

      {/* 4 · SQL */}
      <Section title="SQL" hint="agent's primary extract vs the golden's">
        <div style={twoCol}>
          <div>
            <span style={label}>agent sql_text</span>
            <Code text={primarySql} />
          </div>
          <div>
            <span style={label}>golden_sql</span>
            <Code text={result.golden_sql} />
          </div>
        </div>
        {trace.sql.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <span style={label}>extracts this run ({trace.sql.length})</span>
            {trace.sql.map((st, i) => (
              <div key={i} style={{ display: "flex", gap: 10, alignItems: "baseline", fontFamily: mono, fontSize: 11, color: st.status === "success" ? "var(--muted)" : "var(--bad)", marginTop: 3 }}>
                <span>{st.frame ?? "—"}</span>
                <span>{st.row_count === null || st.row_count === undefined ? "—" : `${st.row_count} rows`}</span>
                <span>{st.status ?? "—"}</span>
                {st.purpose && <span style={{ color: "var(--faint)", fontFamily: "inherit" }}>{st.purpose}</span>}
                {st.error && <span>{st.error}</span>}
              </div>
            ))}
            {otherSql.length > 0 && <Code text={otherSql.map((st) => st.sql ?? "").join("\n\n-- ---\n\n")} collapsed summary="other extract SQL" />}
          </div>
        )}
      </Section>

      {/* 5 · sandbox */}
      <Section title="SANDBOX" hint={trace.analysis ? `${trace.analysis.runtime ?? "—"} · ${trace.analysis.ms ?? "—"} ms · ${trace.analysis.passes.length} pass(es)` : "no analysis recorded"}>
        <div style={twoCol}>
          <div>
            <span style={label}>agent run_analysis passes</span>
            {trace.analysis && trace.analysis.passes.length > 0 ? (
              trace.analysis.passes.map((p, i) => <PassView key={i} pass={p} index={i} />)
            ) : (
              <div><Dash value={null} /></div>
            )}
            {trace.analysis && trace.analysis.skill_gaps.length > 0 && (
              <KV k="skill_gaps" v={<code style={{ fontSize: 11 }}>{JSON.stringify(trace.analysis.skill_gaps)}</code>} warn />
            )}
            {trace.analysis?.used_inline_math && <KV k="inline math" v="used — the agent did maths outside a skill" warn />}
          </div>
          <div>
            <span style={label}>golden_sandbox</span>
            <Code text={result.golden_sandbox} collapsed summary="golden sandbox code" />
          </div>
        </div>
      </Section>

      {/* 6 · deck */}
      <Section title="DECK" hint={manifest ? `${slides.length} slide(s)` : "no manifest"}>
        {slides.length > 0 ? (
          <div style={{ overflowX: "auto", marginTop: 6 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr>
                  {["#", "layout", "headline", "kpi", "chart", "rows"].map((h) => (
                    <th key={h} style={{ ...label, textAlign: "left", padding: "0 6px 4px" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {slides.map((sl, i) => (
                  <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ padding: "4px 6px", fontFamily: mono }}>{sl.index ?? i}</td>
                    <td style={{ padding: "4px 6px", fontFamily: mono }}>{sl.layout ?? "—"}</td>
                    <td style={{ padding: "4px 6px", whiteSpace: "normal" }}>
                      {sl.slide_url ? <a href={sl.slide_url} target="_blank" rel="noreferrer">{sl.headline ?? "—"}</a> : sl.headline ?? "—"}
                    </td>
                    <td style={{ padding: "4px 6px", whiteSpace: "normal" }}>
                      {sl.spec?.kpi ? <span style={{ fontFamily: mono }}>{sl.spec.kpi}</span> : "—"}
                      {sl.spec?.kpi_label && <span style={{ color: "var(--faint)", fontSize: 11 }}> {sl.spec.kpi_label}</span>}
                    </td>
                    <td style={{ padding: "4px 6px", fontFamily: mono }}>{sl.spec?.chart_type || sl.spec?.rendered_as || "—"}</td>
                    <td style={{ padding: "4px 6px", fontFamily: mono }}>{sl.rows ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div style={{ marginTop: 6 }}><Dash value={null} /></div>
        )}
        {(deckUrl || sheetUrl) && (
          <div style={{ display: "flex", gap: 12, marginTop: 8, fontSize: 12 }}>
            {deckUrl && <a href={deckUrl} target="_blank" rel="noreferrer">open deck ↗</a>}
            {sheetUrl && <a href={sheetUrl} target="_blank" rel="noreferrer">open sheet ↗</a>}
          </div>
        )}
      </Section>

      {/* 7 · links */}
      <Section title="LINKS">
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginTop: 8, fontSize: 12, fontFamily: mono }}>
          <span>
            <span style={label}>query run </span>
            <Dash value={result.query_run_id} />
          </span>
          <span>
            <span style={label}>mlflow trace </span>
            {result.otel_trace_id ? (
              <a href={`${base}/#/experiments/${traceExp}/traces?selectedTraceId=tr-${result.otel_trace_id}`} target="_blank" rel="noreferrer">
                tr-{result.otel_trace_id.slice(0, 12)}… ↗
              </a>
            ) : (
              <Dash value={null} />
            )}
          </span>
          <span>
            <span style={label}>mlflow case run </span>
            {result.mlflow_run_id ? (
              <a href={`${base}/#/experiments/2/runs/${result.mlflow_run_id}`} target="_blank" rel="noreferrer">
                {result.mlflow_run_id.slice(0, 12)}… ↗
              </a>
            ) : (
              <Dash value={null} />
            )}
          </span>
        </div>
      </Section>
    </div>
  );
}

const COLUMNS = ["", "case", "G1", "judge", "tokens", "cost", "latency", "turns"];
// Fixed layout: the case column absorbs the slack and wraps, so the spend
// columns (and the drill-down row, which spans them all) never push the table
// wider than the panel.
const COLUMN_WIDTHS = [96, undefined, 52, 168, 128, 78, 70, 52];

function CaseRow({
  result,
  run,
  open,
  onToggle,
}: {
  result: EvalCaseResult;
  run: EvalRun;
  open: boolean;
  onToggle: () => void;
}) {
  const issues = result.g3?.format?.issues ?? [];
  const cell: React.CSSProperties = { padding: "8px 6px", fontFamily: mono, fontSize: 12 };
  return (
    <Fragment>
      <tr
        role="button"
        tabIndex={0}
        aria-expanded={open}
        data-testid={`eval-case-${result.case_key}`}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
        style={{
          borderBottom: open ? undefined : "1px solid var(--border)",
          cursor: "pointer",
          background: open ? "var(--panel)" : undefined,
        }}
      >
        <td style={{ padding: "8px 6px", whiteSpace: "nowrap" }}>
          <span style={{ color: "var(--faint)", fontSize: 10, marginRight: 6, display: "inline-block", width: 8 }}>
            {open ? "▾" : "▸"}
          </span>
          <Annunciator state={result.passed ? "on" : "bad"}>
            {result.passed ? "PASS" : "FAIL"}
          </Annunciator>
          {result.holdout && (
            <span style={{ fontSize: 10, color: "var(--accent)", marginLeft: 6 }}>holdout</span>
          )}
        </td>
        <td style={{ padding: "8px 6px", minWidth: 0, overflowWrap: "anywhere", whiteSpace: "normal" }}>
          <div style={{ fontSize: 13 }}>{result.question}</div>
          <div style={{ fontFamily: mono, fontSize: 10.5, color: "var(--faint)" }}>
            {result.case_key} · {result.tier}
          </div>
          {result.notes && (
            <div style={{ fontSize: 11.5, color: "var(--bad)", marginTop: 2 }}>{result.notes}</div>
          )}
          {issues.length > 0 && (
            <div style={{ fontSize: 11.5, color: "var(--warn)", marginTop: 2 }}>
              {issues.join("; ")}
            </div>
          )}
          <Checkpoints checkpoints={result.checkpoints} />
        </td>
        <td style={cell}>
          {result.g1?.score === null || result.g1?.score === undefined
            ? "—"
            : fmt(result.g1.score)}
        </td>
        <td style={{ padding: "8px 6px" }}>
          <JudgeBadge judge={result.judge} />
        </td>
        <td style={{ ...cell, whiteSpace: "nowrap" }}>
          {fmtInt(result.input_tokens)} / {fmtInt(result.output_tokens)}
          {result.cache_read_tokens ? (
            <div style={{ fontSize: 10.5, color: "var(--faint)" }}>+{fmtInt(result.cache_read_tokens)} cached</div>
          ) : null}
        </td>
        <td style={{ ...cell, whiteSpace: "nowrap" }}>{fmtCost(result.cost_usd)}</td>
        <td style={{ ...cell, whiteSpace: "nowrap" }}>{fmtSecs(result.latency_ms ?? result.g4?.latency_ms)}</td>
        <td style={cell}>{result.g4?.turns ?? "—"}</td>
      </tr>
      {open && (
        <tr style={{ borderBottom: "1px solid var(--border)" }}>
          {/* The global td rule is nowrap (styles.css); prose in here must wrap. */}
          <td colSpan={COLUMNS.length} style={{ padding: "0 6px", whiteSpace: "normal" }}>
            <CaseDrilldown result={result} run={run} />
          </td>
        </tr>
      )}
    </Fragment>
  );
}

export function EvalsPage() {
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<EvalRunDetail | null>(null);
  const [msg, setMsg] = useState("");
  // s50: which case's drill-down is open — one at a time, so two panels of
  // SQL and sandbox code never fight for the screen.
  const [openCase, setOpenCase] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const rows = await getEvalRuns();
      setRuns(rows);
      setSelected((current) => current ?? rows[0]?.id ?? null);
    } catch (e) {
      setMsg((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!selected) return;
    setOpenCase(null);
    getEvalRun(selected)
      .then(setDetail)
      .catch((e) => setMsg((e as Error).message));
  }, [selected]);

  const totals = detail?.run.totals ?? {};
  const cmp = detail?.comparison ?? null;

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(0,240px) minmax(0,1fr)",
        gap: 16,
        // The view host clips at its top edge, so without this the first run
        // card's title is cut in half.
        padding: "18px 20px 40px",
        // The view host is a min-height:0 flex column that never scrolls
        // itself (other tabs scroll inside <main>); with the s50 drill-down
        // the page is taller than the viewport, so this element must.
        flex: 1,
        minHeight: 0,
        overflowY: "auto",
        alignContent: "start",
      }}
    >
      <aside style={{ minWidth: 0 }}>
        <div style={label}>runs</div>
        {runs.length === 0 && (
          <p style={{ fontSize: 13, color: "var(--muted)" }}>
            No eval runs yet — score the pack with <code>make eval</code>.
          </p>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 8 }}>
          {runs.map((run) => (
            <button
              key={run.id}
              onClick={() => setSelected(run.id)}
              style={{
                ...card,
                textAlign: "left",
                cursor: "pointer",
                // Global button styling washes out inherited text, which made
                // the run label almost unreadable — set both explicitly.
                color: "var(--text)",
                font: "inherit",
                borderColor: run.id === selected ? "var(--accent)" : "var(--border)",
              }}
            >
              <div
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  color: run.id === selected ? "var(--accent)" : "var(--text)",
                }}
              >
                {runTitle(run)}
              </div>
              <div style={{ fontFamily: mono, fontSize: 10.5, color: "var(--faint)" }}>
                {run.pack_version} · {run.totals.passed ?? 0}/{run.totals.cases ?? 0} pass
              </div>
              <div style={{ fontSize: 10.5, color: "var(--faint)" }}>
                {run.started_at ? new Date(run.started_at).toLocaleString() : ""}
              </div>
            </button>
          ))}
        </div>
      </aside>

      <section style={{ minWidth: 0 }}>
        {msg && <p style={{ color: "var(--bad)", fontSize: 13 }}>{msg}</p>}
        {!detail && <p style={{ color: "var(--muted)" }}>Select a run.</p>}
        {detail && (
          <>
            <div
              style={{
                display: "flex",
                gap: 12,
                flexWrap: "wrap",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <div style={{ minWidth: 0 }}>
                <div style={label}>
                  {runTitle(detail.run)} · pack {detail.run.pack_version} ·{" "}
                  {totals.cases ?? 0} case(s)
                </div>
                <div style={{ fontFamily: mono, fontSize: 11.5, color: "var(--muted)", marginTop: 3 }}>
                  B {detail.run.agent.fingerprint ?? "unstamped"}
                  {cmp?.base && ` vs A ${cmp.base.agent.fingerprint ?? "unstamped"}`}
                </div>
                {detail.run.hypothesis && (
                  <div style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 3 }}>
                    hypothesis: {detail.run.hypothesis}
                  </div>
                )}
              </div>
              {cmp && <GateBadge gate={cmp.gate} comparable={cmp.comparable} />}
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
                gap: 10,
                marginTop: 14,
              }}
            >
              <Pillar
                name="overall pass"
                value={`${Math.round((totals.pass_rate ?? 0) * 100)}%`}
                hint={`${totals.passed ?? 0} of ${totals.cases ?? 0}`}
              />
              <Pillar name="G1 extraction" value={fmt(totals.g1_mean)} />
              <Pillar
                name="judge · high"
                value={`${totals.judge_labels?.high ?? 0}/${totals.cases ?? 0}`}
                hint={
                  totals.judge_calibration?.calibrated
                    ? `calibrated · ${detail.run.judge_model || "judge"}`
                    : `NOT calibrated · ${totals.judge_calibration?.agreed ?? 0}/${
                        totals.judge_calibration?.probes ?? 0
                      } probes`
                }
              />
              <Pillar
                name="G4 turns"
                value={fmt(totals.g4_turns_mean, 1)}
                hint="cost driver"
              />
              <Pillar
                name="tokens"
                value={
                  detail.run.tokens
                    ? `${fmtInt(detail.run.tokens.input)} / ${fmtInt(detail.run.tokens.output)}`
                    : "—"
                }
                hint={
                  detail.run.tokens
                    ? `in / out · +${fmtInt(detail.run.tokens.cache_read)} cache read`
                    : "no query_runs"
                }
              />
              <Pillar
                name="cost"
                value={detail.run.tokens ? fmtCost(detail.run.tokens.cost_usd) : "—"}
                hint="priced with the cache split"
              />
            </div>

            {/* Honesty about what a small corpus can prove — the same note the
                runner prints, surfaced where the numbers are read. */}
            {/* The judge is advisory below HOLDOUT_MIN_CASES goldens (decision
                D2). Saying so where the labels are read is the difference
                between an honest signal and a number people start trusting. */}
            {totals.judge_gates === false && (totals.judge_labels?.high ?? 0) + (totals.judge_labels?.medium ?? 0) + (totals.judge_labels?.low ?? 0) > 0 && (
              <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 10 }}>
                Judge labels are advisory on this pack — PASS/FAIL is decided by G1 and the
                delivered deck alone.
              </p>
            )}
            {totals.judge_calibration && !totals.judge_calibration.calibrated && (
              <p style={{ fontSize: 12, color: "var(--warn)", marginTop: 6 }}>
                The judge did not reproduce the pack's known labels on this run
                ({totals.judge_calibration.agreed ?? 0}/{totals.judge_calibration.probes ?? 0}{" "}
                probes agreed), so read every label below as noise until it does.
              </p>
            )}
            {totals.generalisation === "unproven" && (
              <p style={{ fontSize: 12, color: "var(--warn)", marginTop: 10 }}>
                Fewer than 10 cases — no holdout slice, so an improvement here is not yet
                evidence that it generalises.
              </p>
            )}
            {cmp && !cmp.comparable && (
              <p style={{ fontSize: 12, color: "var(--warn)", marginTop: 6 }}>
                This run and its baseline were graded against different packs, so the deltas
                are not a like-for-like measurement.
              </p>
            )}
            {cmp && (cmp.fixed.length > 0 || cmp.regressed.length > 0) && (
              <p style={{ fontSize: 12.5, marginTop: 8 }}>
                {cmp.fixed.length > 0 && (
                  <span style={{ color: "var(--good)" }}>fixed: {cmp.fixed.join(", ")} </span>
                )}
                {cmp.regressed.length > 0 && (
                  <span style={{ color: "var(--bad)" }}>
                    regressed: {cmp.regressed.join(", ")}
                  </span>
                )}
              </p>
            )}

            <div style={{ overflowX: "auto", marginTop: 16 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed" }}>
                <colgroup>
                  {COLUMN_WIDTHS.map((w, i) => (
                    <col key={COLUMNS[i]} style={w ? { width: w } : undefined} />
                  ))}
                </colgroup>
                <thead>
                  <tr>
                    {COLUMNS.map((h) => (
                      <th key={h} style={{ ...label, textAlign: "left", padding: "0 6px 6px" }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {detail.results.map((r) => (
                    <CaseRow
                      key={r.case_key}
                      result={r}
                      run={detail.run}
                      open={openCase === r.case_key}
                      onToggle={() =>
                        setOpenCase((cur) => (cur === r.case_key ? null : r.case_key))
                      }
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
