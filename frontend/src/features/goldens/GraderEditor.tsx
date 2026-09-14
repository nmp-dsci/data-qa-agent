// The grader-spec editor (Golden Examples tab). Turns the free-form
// `eval_cases.grader` jsonb into grain-driven dropdowns and gates promotion
// draft → ready on a deterministic, no-LLM readiness check that mirrors the CI
// pack-lint. A golden is only "ready" (scoreable by the eval runner) once every
// column the grader names exists in the ① SQL extract and the kind's required
// fields are set — exactly what graderIssue() enforces here and CI enforces in
// tests/test_eval_pack.py.
import type React from "react";
import { KitMultiSelect } from "@/components/kit/KitMultiSelect";
import { KitSelect } from "@/components/kit/KitSelect";
import type {
  CalibrationExample,
  GoldenCheckpoints,
  GoldenLabel,
  GraderSpec,
} from "../../lib/api";
import {
  GRADER_KIND_INFO,
  GRADER_KINDS,
  type GraderKind,
  graderIssue,
  keyColumns,
  namedColumns,
  TIER_DEFAULT_KIND,
  withKeyColumns,
} from "./graderSpec";

const box: React.CSSProperties = {
  border: "1px solid var(--border)",
  background: "var(--panel)",
  borderRadius: 10,
  padding: "12px 14px",
};
const label: React.CSSProperties = {
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: 0.5,
  opacity: 0.7,
};
const num: React.CSSProperties = { fontSize: 12, padding: "2px 4px", width: 70 };
const field: React.CSSProperties = { display: "flex", flexDirection: "column", gap: 3 };

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

/** Option list that always keeps any current value present even if it's not in
 *  the offered set (so a saved grader never loses a column the extract dropped). */
function options(current: string[], opts: string[]) {
  const extra = current.filter((c) => c && !opts.includes(c));
  return [...extra, ...opts].map((c) => ({ value: c, label: c }));
}

/** Comma/newline-separated free text ⇄ a string list. Chips would be nicer, but
 *  these lists name skills and columns the curator often pastes from a trace, so
 *  a textarea is the faster surface and loses nothing. */
function toList(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((t) => t.trim())
    .filter(Boolean);
}

const LABELS: GoldenLabel[] = ["high", "medium", "low"];

const area: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  fontSize: 12.5,
  padding: 8,
  background: "var(--panel-2)",
  color: "var(--text)",
  border: "1px solid var(--border-2)",
  borderRadius: 8,
};

interface GraderEditorProps {
  grader: GraderSpec;
  onChange: (g: GraderSpec) => void;
  /** Columns the ① SQL extract produces — the grader may only name these. */
  columns: string[];
  tier: string;
  status: string;
  onStatusChange: (status: string) => void;
  /** s49 M2 — golden v2: the reference answer the judge grades against, the
   *  label it must return for it, calibration examples, and the diagnostic
   *  checkpoints. All optional: a golden with none of them is still scoreable,
   *  it just has no judge reference and no per-stage diagnosis. */
  goldenAnswer: string;
  onGoldenAnswerChange: (text: string) => void;
  label: GoldenLabel | "";
  onLabelChange: (label: GoldenLabel) => void;
  calibrationExamples: CalibrationExample[];
  onCalibrationExamplesChange: (examples: CalibrationExample[]) => void;
  checkpoints: GoldenCheckpoints;
  onCheckpointsChange: (checkpoints: GoldenCheckpoints) => void;
}

export function GraderEditor({
  grader,
  onChange,
  columns,
  tier,
  status,
  onStatusChange,
  goldenAnswer,
  onGoldenAnswerChange,
  label: goldenLabel,
  onLabelChange,
  calibrationExamples,
  onCalibrationExamplesChange,
  checkpoints,
  onCheckpointsChange,
}: GraderEditorProps) {
  const g = grader ?? {};
  const set = (patch: Partial<GraderSpec>) => onChange({ ...g, ...patch });
  const kind = g.kind ?? "";
  const keys = keyColumns(g);
  const blocker = graderIssue(g, columns);
  const ready = status === "ready";
  const suggested = TIER_DEFAULT_KIND[tier];
  const needsKey = kind === "row_set" || kind === "ranked_set" || kind === "series";

  return (
    <div style={box} data-testid="grader-editor">
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "rgb(120,160,255)" }}>
          ◆ GRADER — how this golden is scored
        </span>
        <span style={label}>draft → ready · deterministic (no LLM) · mirrors the eval pack-lint</span>
      </div>

      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 10, alignItems: "flex-end" }}>
        {/* kind */}
        <div style={field}>
          <span style={label}>kind {kind ? "" : suggested ? `(${tier} → ${suggested})` : ""}</span>
          <KitSelect
            testId="grader-kind"
            ariaLabel="Grader kind"
            value={kind}
            onValueChange={(v) => set({ kind: (v || "") as GraderKind | "" })}
            options={[
              { value: "", label: "— pick —" },
              ...GRADER_KINDS.map((k) => ({ value: k, label: GRADER_KIND_INFO[k].label })),
            ]}
          />
        </div>

        {/* key column(s) — one → key, many → composite _key */}
        {needsKey && (
          <div style={field}>
            <span style={label}>key column(s){keys.length > 1 ? " — composite" : ""}</span>
            <KitMultiSelect
              testId="grader-key"
              ariaLabel="Grader key columns"
              values={keys}
              onValuesChange={(vals) => onChange(withKeyColumns(g, vals))}
              options={options(keys, columns)}
              className="min-w-40"
            />
          </div>
        )}

        {/* value — series compares it; sum aggregates it */}
        {(kind === "series" || g.aggregate === "sum") && (
          <div style={field}>
            <span style={label}>value column</span>
            <KitSelect
              testId="grader-value"
              ariaLabel="Grader value column"
              value={g.value ?? ""}
              onValueChange={(v) => set({ value: v })}
              options={[{ value: "", label: "— pick —" }, ...options(g.value ? [g.value] : [], columns)]}
            />
          </div>
        )}

        {/* top-k cutoff */}
        {kind === "ranked_set" && (
          <div style={field}>
            <span style={label}>top-k</span>
            <input
              data-testid="grader-k"
              type="number"
              min={1}
              style={num}
              value={g.k ?? 5}
              onChange={(e) => set({ k: Number(e.target.value) || 5 })}
            />
          </div>
        )}

        {/* tolerance */}
        {(kind === "scalar" || kind === "series") && (
          <div style={field}>
            <span style={label}>tolerance %</span>
            <input
              data-testid="grader-tolerance"
              type="number"
              min={0}
              step={0.5}
              style={num}
              value={g.tolerance_pct ?? 1}
              onChange={(e) => set({ tolerance_pct: Number(e.target.value) })}
            />
          </div>
        )}

        {/* aggregate — a runner pre-transform that rolls both sides to the key
            grain. ratio rebuilds value = numerator / denominator (a weighted
            average), so it's graded instead of an average-of-averages. */}
        {needsKey && (
          <div style={field}>
            <span style={label}>aggregate</span>
            <KitSelect
              testId="grader-aggregate"
              ariaLabel="Grader aggregate"
              value={g.aggregate ?? ""}
              onValueChange={(v) => set({ aggregate: (v || "") as "sum" | "ratio" | "" })}
              options={[
                { value: "", label: "— none —" },
                { value: "sum", label: "sum" },
                { value: "ratio", label: "ratio (num / den)" },
              ]}
            />
          </div>
        )}

        {g.aggregate === "ratio" && (
          <>
            <div style={field}>
              <span style={label}>numerator</span>
              <KitSelect
                testId="grader-numerator"
                ariaLabel="Grader numerator column"
                value={g.numerator ?? ""}
                onValueChange={(v) => set({ numerator: v })}
                options={[{ value: "", label: "— pick —" }, ...options(g.numerator ? [g.numerator] : [], columns)]}
              />
            </div>
            <div style={field}>
              <span style={label}>denominator</span>
              <KitSelect
                testId="grader-denominator"
                ariaLabel="Grader denominator column"
                value={g.denominator ?? ""}
                onValueChange={(v) => set({ denominator: v })}
                options={[{ value: "", label: "— pick —" }, ...options(g.denominator ? [g.denominator] : [], columns)]}
              />
            </div>
          </>
        )}

        {/* G5 — the deck the user received: at least N slides, and a chart or
            table somewhere. Layout identity is never graded (the agent chooses). */}
        <div style={field}>
          <span style={label}>min slides</span>
          <input
            data-testid="grader-min-slides"
            type="number"
            min={0}
            style={num}
            value={g.min_slides ?? 1}
            onChange={(e) => set({ min_slides: Math.max(0, Number(e.target.value) || 0) })}
          />
        </div>
        <div style={field}>
          <span style={label}>expect chart</span>
          <label style={{ display: "flex", alignItems: "center", gap: 6, minHeight: 32 }}>
            <input
              data-testid="grader-expect-chart"
              type="checkbox"
              checked={g.expect_chart ?? true}
              onChange={(e) => set({ expect_chart: e.target.checked })}
            />
            <span className="muted">a chart or table on some slide</span>
          </label>
        </div>
      </div>

      {/* ── Golden v2 (s49 M2): the judge's reference + the diagnostic
          checkpoints. Kept below the grader because the grader is what gates:
          nothing in this block can fail a case, and the copy says so. */}
      <div style={{ marginTop: 14, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: "rgb(120,160,255)" }}>
            ◆ REFERENCE ANSWER — what the judge grades against
          </span>
          <span style={label}>advisory · never gates a case</span>
        </div>

        <div style={{ display: "flex", gap: 14, marginTop: 8, alignItems: "flex-start" }}>
          <div style={{ ...field, flex: 1, minWidth: 0 }}>
            <span style={label}>golden answer</span>
            <textarea
              data-testid="golden-answer"
              value={goldenAnswer}
              onChange={(e) => onGoldenAnswerChange(e.target.value)}
              rows={5}
              placeholder="The answer a domain expert would give, with the numbers in it."
              style={area}
            />
          </div>
          <div style={field}>
            <span style={label}>label</span>
            <KitSelect
              testId="golden-label"
              ariaLabel="Reference answer label"
              value={goldenLabel || "high"}
              onValueChange={(v) => onLabelChange((v || "high") as GoldenLabel)}
              options={LABELS.map((l) => ({ value: l, label: l }))}
            />
            <span style={{ ...label, opacity: 0.6, maxWidth: 150 }}>
              what the judge must return for this text
            </span>
          </div>
        </div>

        {/* Calibration examples — answers with known labels the judge must
            reproduce before any of its labels are trusted. */}
        <div style={{ marginTop: 10 }}>
          <span style={label}>calibration examples</span>
          {calibrationExamples.map((example, index) => (
            <div
              // Index keys are correct here: rows are positional and edited in
              // place, never reordered.
              key={index}
              style={{ display: "flex", gap: 8, marginTop: 6, alignItems: "flex-start" }}
            >
              <KitSelect
                testId={`calibration-label-${index}`}
                ariaLabel={`Calibration example ${index + 1} label`}
                value={example.label}
                onValueChange={(v) =>
                  onCalibrationExamplesChange(
                    calibrationExamples.map((e, i) =>
                      i === index ? { ...e, label: (v || "medium") as GoldenLabel } : e,
                    ),
                  )
                }
                options={LABELS.map((l) => ({ value: l, label: l }))}
              />
              <textarea
                data-testid={`calibration-answer-${index}`}
                value={example.answer}
                onChange={(e) =>
                  onCalibrationExamplesChange(
                    calibrationExamples.map((row, i) =>
                      i === index ? { ...row, answer: e.target.value } : row,
                    ),
                  )
                }
                rows={2}
                style={{ ...area, flex: 1 }}
              />
              <button
                type="button"
                style={btn()}
                onClick={() =>
                  onCalibrationExamplesChange(calibrationExamples.filter((_, i) => i !== index))
                }
              >
                ✕
              </button>
            </div>
          ))}
          <button
            type="button"
            data-testid="calibration-add"
            style={{ ...btn(), marginTop: 6 }}
            onClick={() =>
              onCalibrationExamplesChange([
                ...calibrationExamples,
                { label: "medium", answer: "" },
              ])
            }
          >
            + Add example
          </button>
        </div>

        {/* Checkpoints — diagnosis, not gates. Never shown to the agent: a
            checkpoint it can read becomes the goal instead of the answer. */}
        <div
          style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 12, alignItems: "flex-start" }}
        >
          <div style={field}>
            <span style={label}>checkpoint · sql key cols</span>
            <input
              data-testid="checkpoint-key-cols"
              value={(checkpoints.sql?.key_cols ?? []).join(", ")}
              onChange={(e) =>
                onCheckpointsChange({ ...checkpoints, sql: { key_cols: toList(e.target.value) } })
              }
              placeholder="postcode"
              style={{ ...area, width: 190 }}
            />
          </div>
          <div style={field}>
            <span style={label}>checkpoint · expected skills</span>
            <input
              data-testid="checkpoint-skills"
              value={(checkpoints.analysis?.expected_skills ?? []).join(", ")}
              onChange={(e) =>
                onCheckpointsChange({
                  ...checkpoints,
                  analysis: { ...checkpoints.analysis, expected_skills: toList(e.target.value) },
                })
              }
              placeholder="latest_value, growth_rate"
              style={{ ...area, width: 220 }}
            />
          </div>
          <div style={field}>
            <span style={label}>checkpoint · derived cols</span>
            <input
              data-testid="checkpoint-derived-cols"
              value={(checkpoints.analysis?.derived_cols ?? []).join(", ")}
              onChange={(e) =>
                onCheckpointsChange({
                  ...checkpoints,
                  analysis: { ...checkpoints.analysis, derived_cols: toList(e.target.value) },
                })
              }
              placeholder="rent_growth_pct"
              style={{ ...area, width: 190 }}
            />
          </div>
          <div style={field}>
            <span style={label}>checkpoint · layouts any of</span>
            <input
              data-testid="checkpoint-layouts"
              value={(checkpoints.deck?.layouts_any_of ?? []).join(", ")}
              onChange={(e) =>
                onCheckpointsChange({
                  ...checkpoints,
                  deck: { ...checkpoints.deck, layouts_any_of: toList(e.target.value) },
                })
              }
              placeholder="Title + Chart"
              style={{ ...area, width: 190 }}
            />
          </div>
          <div style={field}>
            <span style={label}>checkpoint · kpi label contains</span>
            <input
              data-testid="checkpoint-kpi-label"
              value={checkpoints.deck?.kpi_label_contains ?? ""}
              onChange={(e) =>
                onCheckpointsChange({
                  ...checkpoints,
                  deck: { ...checkpoints.deck, kpi_label_contains: e.target.value },
                })
              }
              placeholder="rent"
              style={{ ...area, width: 170 }}
            />
          </div>
        </div>
      </div>

      {kind === "series" && columns.length === 0 && (
        <div style={{ ...label, marginTop: 8, opacity: 0.6 }}>
          ▶ Run SQL first so the key/value columns can be verified against the extract.
        </div>
      )}

      {/* readiness + promote */}
      <div
        style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12, flexWrap: "wrap" }}
      >
        {ready ? (
          <>
            <span
              data-testid="grader-status"
              style={{ fontSize: 12.5, color: blocker ? "var(--bad, #e5484d)" : "var(--good, #46a758)" }}
            >
              {blocker ? `● ready, but grader no longer valid: ${blocker}` : "● ready — scoreable by the eval runner"}
            </span>
            <button
              type="button"
              data-testid="grader-demote"
              style={btn()}
              onClick={() => onStatusChange("draft")}
            >
              ↩ Demote to draft
            </button>
          </>
        ) : (
          <>
            <span
              data-testid="grader-check"
              style={{ fontSize: 12.5, color: blocker ? "var(--bad, #e5484d)" : "var(--good, #46a758)" }}
            >
              {blocker ? `✗ ${blocker}` : "✓ ready to promote · deterministic"}
            </span>
            <button
              type="button"
              data-testid="grader-promote"
              style={btn(!blocker)}
              disabled={!!blocker}
              onClick={() => onStatusChange("ready")}
              title={blocker ?? "mark this golden ready — it enters the scored eval set"}
            >
              ⭑ Promote to ready
            </button>
          </>
        )}
        {!blocker && kind && (
          <span style={{ ...label, opacity: 0.6 }}>
            grades: {namedColumns(g).join(", ") || "(structure only)"}
          </span>
        )}
      </div>
    </div>
  );
}
