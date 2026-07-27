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
import type { GraderSpec } from "../../lib/api";
import {
  GRADER_KIND_INFO,
  GRADER_KINDS,
  type GraderKind,
  graderIssue,
  keyColumns,
  namedColumns,
  REPORT_OBJECT_TYPES,
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

interface GraderEditorProps {
  grader: GraderSpec;
  onChange: (g: GraderSpec) => void;
  /** Columns the ① SQL extract produces — the grader may only name these. */
  columns: string[];
  /** Object types present in the current ③ report (expected_objects suggestions). */
  reportObjectTypes: string[];
  tier: string;
  status: string;
  onStatusChange: (status: string) => void;
}

export function GraderEditor({
  grader,
  onChange,
  columns,
  reportObjectTypes,
  tier,
  status,
  onStatusChange,
}: GraderEditorProps) {
  const g = grader ?? {};
  const set = (patch: Partial<GraderSpec>) => onChange({ ...g, ...patch });
  const kind = g.kind ?? "";
  const keys = keyColumns(g);
  const blocker = graderIssue(g, columns);
  const ready = status === "ready";
  const suggested = TIER_DEFAULT_KIND[tier];
  const needsKey = kind === "row_set" || kind === "ranked_set" || kind === "series";

  const objTypeOpts = Array.from(new Set<string>([...REPORT_OBJECT_TYPES, ...reportObjectTypes]));

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

        {/* expected objects — G3 structural: the report must contain these types */}
        <div style={field}>
          <span style={label}>
            expected objects{reportObjectTypes.length ? ` · report has: ${reportObjectTypes.join(", ")}` : ""}
          </span>
          <KitMultiSelect
            testId="grader-expected-objects"
            ariaLabel="Expected report objects"
            values={g.expected_objects ?? []}
            onValuesChange={(vals) => set({ expected_objects: vals })}
            options={objTypeOpts.map((o) => ({ value: o, label: o }))}
            className="min-w-40"
          />
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
