// Evaluations tab (s24 M4) — the read surface over scored eval runs.
//
// Left: every run, newest first, so quality over time is visible rather than
// anecdotal. Right: one run — its build fingerprint, pillar scores, the
// regression gate against its baseline when it is an experiment, and per-case
// results each linking to the query_runs trace that produced it.
//
// Deliberately read-only. Runs come from `make eval`; nothing here can produce
// a score, so the dashboard can never disagree with the recorded evidence.

import { useCallback, useEffect, useState } from "react";
import {
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

function CaseRow({ result }: { result: EvalCaseResult }) {
  const insight = result.g3?.insight;
  const issues = result.g3?.format?.issues ?? [];
  return (
    <tr style={{ borderBottom: "1px solid var(--border)" }}>
      <td style={{ padding: "8px 6px" }}>
        <Annunciator state={result.passed ? "on" : "bad"}>
          {result.passed ? "PASS" : "FAIL"}
        </Annunciator>
        {result.holdout && (
          <span style={{ fontSize: 10, color: "var(--accent)", marginLeft: 6 }}>holdout</span>
        )}
      </td>
      <td style={{ padding: "8px 6px", minWidth: 0 }}>
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
      </td>
      <td style={{ padding: "8px 6px", fontFamily: mono, fontSize: 12 }}>
        {result.g1?.score === null || result.g1?.score === undefined
          ? "—"
          : fmt(result.g1.score)}
      </td>
      <td style={{ padding: "8px 6px", fontFamily: mono, fontSize: 12 }}>
        {insight?.total === null || insight?.total === undefined
          ? "—"
          : `${insight.total}/${insight.max ?? 10}`}
      </td>
      <td style={{ padding: "8px 6px", fontFamily: mono, fontSize: 12 }}>{result.g4?.turns ?? "—"}</td>
    </tr>
  );
}

export function EvalsPage() {
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<EvalRunDetail | null>(null);
  const [msg, setMsg] = useState("");

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
                name="G3 insight"
                value={fmt(totals.g3_insight_mean, 1)}
                hint={detail.run.judge_model ? detail.run.judge_model : "no judge configured"}
              />
              <Pillar
                name="G4 turns"
                value={fmt(totals.g4_turns_mean, 1)}
                hint="cost driver"
              />
            </div>

            {/* Honesty about what a small corpus can prove — the same note the
                runner prints, surfaced where the numbers are read. */}
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
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr>
                    {["", "case", "G1", "insight", "turns"].map((h) => (
                      <th key={h} style={{ ...label, textAlign: "left", padding: "0 6px 6px" }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {detail.results.map((r) => (
                    <CaseRow key={r.case_key} result={r} />
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
