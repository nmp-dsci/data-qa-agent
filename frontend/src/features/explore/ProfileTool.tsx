// ProfileTool — the cohort comparison. Two cohort rows (Target gold / Comparison
// blue), a shared response metric, and (Ask-AI aside) a Run button. The result
// arrives from the backend already assembled as report-engine pages (s20:
// choropleth, KPI tiles, comparison/filter/uplift tables, per-predictor charts)
// and renders through the same PageLayout as chat answers and goldens.
import { memo, useEffect, useState } from "react";
import {
  createGolden,
  ExploreDataset,
  ExploreFilters,
  exploreProfile,
  ProfileResult,
} from "../../lib/api";
import { PageLayout } from "../../report-engine/PageLayout";
import { PlaneGlyph } from "../../ui/icons";
import { AskBox } from "./AskBox";
import { FilterEditor, MetricSelect } from "./controls";

/** A period-over-period comparison of a cohort: copy its filters and step the
 *  time dimension (financial or calendar year) back one — FY-on-FY / CY-on-CY. */
function priorPeriod(filters: ExploreFilters): ExploreFilters {
  const next: ExploreFilters = { ...filters };
  for (const dim of ["year_fy", "year"]) {
    if (typeof next[dim] === "number") next[dim] = (next[dim] as number) - 1;
  }
  return next;
}

export function ProfileTool({
  dataset,
  isAdmin = false,
}: {
  dataset: ExploreDataset;
  isAdmin?: boolean;
}) {
  const [metric, setMetric] = useState(dataset.default_metric);
  const [target, setTargetRaw] = useState<ExploreFilters>({});
  const [comparison, setComparisonRaw] = useState<ExploreFilters>({});
  // Until the user edits the comparison, it auto-mirrors the target as the prior
  // period, so setting Target FY=2022 defaults Comparison to FY=2021.
  const [comparisonTouched, setComparisonTouched] = useState(false);
  const [result, setResult] = useState<ProfileResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function setTarget(next: ExploreFilters) {
    setTargetRaw(next);
    if (!comparisonTouched) setComparisonRaw(priorPeriod(next));
  }
  function setComparison(next: ExploreFilters) {
    setComparisonTouched(true);
    setComparisonRaw(next);
  }
  function copyToComparison() {
    setComparisonTouched(true);
    setComparisonRaw(priorPeriod(target));
  }

  // Reset when the dataset changes.
  useEffect(() => {
    setMetric(dataset.default_metric);
    setTargetRaw({});
    setComparisonRaw({});
    setComparisonTouched(false);
    setResult(null);
    setError(null);
  }, [dataset.slug, dataset.default_metric]);

  async function run() {
    setLoading(true);
    setError(null);
    try {
      const res = await exploreProfile({
        dataset: dataset.slug,
        metric,
        target: { filters: target },
        comparison: { filters: comparison },
      });
      setResult(res);
    } catch (e) {
      // Drop the previous result: a stale table sitting under a fresh error reads
      // as the current answer (the "Failed to fetch + identical data" confusion).
      setResult(null);
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  function applyAsk(state: Record<string, unknown>) {
    if (typeof state.metric === "string") setMetric(state.metric);
    const t = (state.target as { filters?: ExploreFilters })?.filters;
    const c = (state.comparison as { filters?: ExploreFilters })?.filters;
    // The interpreter fills both cohorts explicitly; treat comparison as touched
    // so the prior-period auto-mirror doesn't clobber it.
    if (t) setTargetRaw(t);
    if (c) {
      setComparisonRaw(c);
      setComparisonTouched(true);
    }
    // Profile is prefill-only — the user reviews then hits Run.
  }

  return (
    <div className="ex-tool">
      <AskBox
        mode="profile"
        dataset={dataset.slug}
        placeholder='e.g. "compare FY2022 and FY2021 weekly rent for houses"'
        onApply={applyAsk}
      />

      {/* Tree structure: one Metric (parent) spanning the two cohort groups
          (Target / Comparison) — same metric, different groups. */}
      <div className="ex-setup">
        <div className="ex-setup-metric">
          <span className="ex-ctrl-label">Metric</span>
          <MetricSelect dataset={dataset} value={metric} onChange={setMetric} label="" />
          <span className="ex-tree-brace" aria-hidden="true" />
        </div>
        <div className="ex-setup-cohorts">
          <div className="ex-cohort-row">
            <span className="ex-cohort-label tone-target">Target</span>
            <FilterEditor dataset={dataset} filters={target} onChange={setTarget} tone="target" />
            <button
              className="ex-copy"
              title="Copy target filters to comparison, stepping the year back one (FY-on-FY / CY-on-CY)"
              onClick={copyToComparison}
            >
              ⇩ copy to comparison
            </button>
          </div>
          <div className="ex-cohort-row">
            <span className="ex-cohort-label tone-comparison">Comparison</span>
            <FilterEditor
              dataset={dataset}
              filters={comparison}
              onChange={setComparison}
              tone="comparison"
            />
          </div>
        </div>
        <button className="ex-run" onClick={run} disabled={loading}>
          {loading ? "Running…" : "Run profile"}
        </button>
      </div>

      {error && <p className="ex-error">{error}</p>}
      {result && isAdmin && <SaveAsGolden dataset={dataset} result={result} />}
      {result && <ProfileResultView result={result} />}
      {!result && !error && (
        /* s25: a parked plane waiting on a flight plan. The empty state still
           says what to do, in the interface's own voice — the brand supplies
           the picture, not the instructions. */
        <div className="ex-empty">
          <PlaneGlyph size={30} className="ex-empty-glyph" />
          <p className="muted ex-hint">
            Set the two cohorts and a metric, then Run — or describe it above and let the assistant
            fill it in.
          </p>
        </div>
      )}
    </div>
  );
}

// Save-as-golden (admin-only): the result IS pages, so a golden is just the
// pages persisted with a question — the exact payoff of the s20 unification.
// The saved draft opens in the Golden editor rendering pixel-identically.
function SaveAsGolden({ dataset, result }: { dataset: ExploreDataset; result: ProfileResult }) {
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [note, setNote] = useState<string | null>(null);
  // Reset when a new result arrives so each run can be saved once.
  useEffect(() => {
    setState("idle");
    setNote(null);
  }, [result]);

  async function save() {
    const pages = result.pages ?? [];
    if (pages.length === 0) return;
    setState("saving");
    try {
      const headline = pages[0]?.headline ?? `${result.metric_label} profile`;
      const res = await createGolden({
        question: `What drove the change in ${result.metric_label} — ${headline}?`,
        dataset: dataset.slug,
        tags: ["explore", "profile"],
        authoring_status: "draft",
        golden_report: { pages },
      });
      setNote(res.id);
      setState("saved");
    } catch (e) {
      setNote((e as Error).message);
      setState("error");
    }
  }

  return (
    <div className="ex-pick-actions">
      <button
        className="ex-secondary"
        data-testid="profile-save-golden"
        onClick={() => void save()}
        disabled={state === "saving" || state === "saved" || !(result.pages ?? []).length}
      >
        {state === "saving" ? "Saving…" : state === "saved" ? "Saved as golden ✓" : "Save as golden"}
      </button>
      {state === "saved" && note && <span className="muted">draft {note.slice(0, 8)} · Goldens tab</span>}
      {state === "error" && note && <span className="ex-error">{note}</span>}
    </div>
  );
}

// Memoized: the result subtree (the 616-shape map + per-predictor charts + tables)
// is expensive, and it only depends on the result — so editing a setup control
// (metric/filters) must not re-render it. Without this, every keystroke in a
// filter box repainted the whole result, which made the controls feel sluggish.
//
// The pages arrive assembled from the backend (app/explore/pages_builder.py) —
// cohort naming, value formatting and layout all happen there, so a Profile
// result renders identically here, in the Golden editor, and anywhere else the
// report engine runs.
const ProfileResultView = memo(function ProfileResultView({ result }: { result: ProfileResult }) {
  const pages = result.pages ?? [];
  if (pages.length === 0) return null;
  return (
    <div className="ex-result">
      {pages.map((page, i) => (
        <section className="ex-result-page" key={i}>
          {page.headline && <p className="page-headline">{page.headline}</p>}
          <PageLayout page={page} />
        </section>
      ))}
    </div>
  );
});
