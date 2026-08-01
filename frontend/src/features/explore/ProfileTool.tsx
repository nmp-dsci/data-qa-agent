// ProfileTool — the cohort comparison. Two cohort rows (Target gold / Comparison
// blue), a shared response metric, and (Ask-AI aside) a Run button. The result
// arrives from the backend already assembled as report-engine pages (s20:
// choropleth, KPI tiles, comparison/filter/uplift tables, per-predictor charts)
// and renders through the same PageLayout as chat answers and goldens.
import { ArrowDownToLine } from "lucide-react";
import { memo, useEffect, useState } from "react";
import { KitSelect } from "@/components/kit/KitSelect";
import {
  createGolden,
  ExploreDataset,
  ExploreFilters,
  exploreProfile,
  ProfileResult,
  track,
} from "../../lib/api";
import { PageLayout } from "../../report-engine/PageLayout";
import { PlaneGlyph } from "../../ui/icons";
import { AskBox } from "./AskBox";
import { FilterEditor } from "./controls";

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
  datasets,
  isAdmin = false,
}: {
  /** The tab's current dataset — the initial pick for both cohorts, and what
   *  the Ask-AI box interprets against. */
  dataset: ExploreDataset;
  /** All datasets the user is granted. Target and Comparison each pick their
   *  own from this list, so a profile can compare across datasets (e.g.
   *  rental bonds vs property sales, both scoped to the same postcode). */
  datasets: ExploreDataset[];
  isAdmin?: boolean;
}) {
  // The dataset each cohort is profiled against. Both start on the tab's
  // current dataset (a same-dataset comparison, the common case); either can
  // be pointed at a different one from its own row.
  const [targetSlug, setTargetSlug] = useState(dataset.slug);
  const [comparisonSlug, setComparisonSlug] = useState(dataset.slug);
  const targetDataset = datasets.find((d) => d.slug === targetSlug) ?? dataset;
  const comparisonDataset = datasets.find((d) => d.slug === comparisonSlug) ?? dataset;

  // Each cohort picks its own metric from its own dataset — Target and
  // Comparison can measure two genuinely different things (e.g. Sold volume
  // vs Bond volume). The "calculation" below is what makes two different
  // metrics comparable at all.
  const [targetMetric, setTargetMetric] = useState(dataset.default_metric);
  const [comparisonMetric, setComparisonMetric] = useState(dataset.default_metric);
  // How the two values are framed against each other. The per-predictor
  // uplift tables/choropleth only run server-side when the two metrics are
  // literally the same one — a segment-by-segment delta between two
  // different measures isn't meaningful.
  const [calculation, setCalculation] = useState<"raw" | "pct_total" | "growth">("raw");

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

  // Reset when the tab's active dataset changes (both cohorts follow it back
  // to a same-dataset comparison — switching tabs is a bigger reset than
  // switching one cohort's dataset from its own row).
  useEffect(() => {
    setTargetSlug(dataset.slug);
    setComparisonSlug(dataset.slug);
    setTargetMetric(dataset.default_metric);
    setComparisonMetric(dataset.default_metric);
    setCalculation("raw");
    setTargetRaw({});
    setComparisonRaw({});
    setComparisonTouched(false);
    setResult(null);
    setError(null);
  }, [dataset.slug, dataset.default_metric]);

  // A cohort's filters may not mean anything against a newly chosen dataset
  // (different dimension names) — clear rather than carry stale filters
  // silently forward. FilterEditor would drop unknown ones anyway; clearing
  // is more honest than a filter chip that quietly vanished. The metric
  // resets to the new dataset's default for the same reason.
  function changeTargetDataset(slug: string) {
    setTargetSlug(slug);
    setTargetRaw({});
    setTargetMetric(datasets.find((d) => d.slug === slug)?.default_metric ?? "");
  }
  function changeComparisonDataset(slug: string) {
    setComparisonSlug(slug);
    setComparisonRaw({});
    setComparisonTouched(true);
    setComparisonMetric(datasets.find((d) => d.slug === slug)?.default_metric ?? "");
  }

  async function run() {
    setLoading(true);
    setError(null);
    try {
      const res = await exploreProfile({
        calculation,
        target: { dataset: targetSlug, metric: targetMetric, filters: target },
        comparison: { dataset: comparisonSlug, metric: comparisonMetric, filters: comparison },
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
    // The NL interpreter only ever fills in one shared metric — apply it to
    // both cohorts (a same-dataset, same-metric comparison is what it builds).
    if (typeof state.metric === "string") {
      setTargetMetric(state.metric);
      setComparisonMetric(state.metric);
    }
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

      {/* Cohorts come first — define WHAT you're comparing. Each cohort owns
          its own Dataset AND Metric picker, so Target and Comparison can be
          two different datasets measuring two different things — e.g. Sold
          volume (sales) vs Bond volume (rentals), both scoped to a postcode.
          Calculation (below) is what makes those two values comparable. */}
      <div className="ex-setup">
        <div className="ex-setup-cohorts">
          <div className="ex-cohort-row">
            <span className="ex-cohort-label tone-target">Target</span>
            <KitSelect
              className="ex-cohort-dataset"
              value={targetSlug}
              ariaLabel="Target dataset"
              onValueChange={(v) => {
                changeTargetDataset(v);
                track("explore_dataset_changed", { dataset: v, cohort: "target" });
              }}
              options={datasets.map((d) => ({ value: d.slug, label: d.name }))}
            />
            <KitSelect
              className="ex-cohort-metric"
              value={targetMetric}
              ariaLabel="Target metric"
              onValueChange={(v) => {
                setTargetMetric(v);
                track("explore_metric_changed", { metric: v, cohort: "target" });
              }}
              options={targetDataset.metrics.map((m) => ({ value: m.name, label: m.label }))}
            />
            <FilterEditor
              dataset={targetDataset}
              filters={target}
              onChange={setTarget}
              tone="target"
            />
            <button
              className="ex-copy"
              title="Copy target filters to comparison, stepping the year back one (FY-on-FY / CY-on-CY)"
              onClick={copyToComparison}
            >
              <ArrowDownToLine size={13} /> copy to comparison
            </button>
          </div>
          <div className="ex-cohort-row">
            <span className="ex-cohort-label tone-comparison">Comparison</span>
            <KitSelect
              className="ex-cohort-dataset"
              value={comparisonSlug}
              ariaLabel="Comparison dataset"
              onValueChange={(v) => {
                changeComparisonDataset(v);
                track("explore_dataset_changed", { dataset: v, cohort: "comparison" });
              }}
              options={datasets.map((d) => ({ value: d.slug, label: d.name }))}
            />
            <KitSelect
              className="ex-cohort-metric"
              value={comparisonMetric}
              ariaLabel="Comparison metric"
              onValueChange={(v) => {
                setComparisonMetric(v);
                track("explore_metric_changed", { metric: v, cohort: "comparison" });
              }}
              options={comparisonDataset.metrics.map((m) => ({ value: m.name, label: m.label }))}
            />
            <FilterEditor
              dataset={comparisonDataset}
              filters={comparison}
              onChange={setComparison}
              tone="comparison"
            />
          </div>
        </div>
        <div className="ex-setup-footer">
          <label className="ex-ctrl">
            <span className="ex-ctrl-label">Calculation</span>
            <KitSelect
              value={calculation}
              ariaLabel="Calculation"
              onValueChange={(v) => setCalculation(v as "raw" | "pct_total" | "growth")}
              options={[
                { value: "raw", label: "Raw value" },
                { value: "pct_total", label: "% of total" },
                { value: "growth", label: "Growth rate" },
              ]}
            />
            {targetMetric !== comparisonMetric && (
              <span className="muted ex-metric-note">
                different metrics — segment breakdowns are skipped
              </span>
            )}
          </label>
          <button
            className="ex-run"
            onClick={run}
            disabled={loading || !targetMetric || !comparisonMetric}
          >
            {loading ? "Running…" : "Run profile"}
          </button>
        </div>
      </div>

      {error && <p className="ex-error">{error}</p>}
      {result && isAdmin && <SaveAsGolden result={result} />}
      {result && <ProfileResultView result={result} />}
      {!result && !error && (
        /* s25: a parked plane waiting on a flight plan. The empty state still
           says what to do, in the interface's own voice — the brand supplies
           the picture, not the instructions. */
        <div className="ex-empty">
          <PlaneGlyph size={30} className="ex-empty-glyph" />
          <p className="muted ex-hint">
            Set each cohort's dataset and metric, pick a calculation, then Run — or describe it
            above and let the assistant fill it in.
          </p>
        </div>
      )}
    </div>
  );
}

// Save-as-golden (admin-only): the result IS pages, so a golden is just the
// pages persisted with a question — the exact payoff of the s20 unification.
// The saved draft opens in the Golden editor rendering pixel-identically.
function SaveAsGolden({ result }: { result: ProfileResult }) {
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
        // A cross-dataset profile has no single dataset to attribute the golden
        // to; target's is the reasonable default (also correct for the common
        // same-dataset case, where target_dataset === comparison_dataset).
        dataset: result.target_dataset,
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
