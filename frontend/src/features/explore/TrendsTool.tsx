// TrendsTool — two side-by-side "chart apps" (legacy trend1/trend2 parity). Each
// has chart-type / metric / split / filter controls that shape a live aggregate
// query; presentation-handover (s46) retired the in-browser chart renderer, so
// the result renders as a plain table of the fetched rows (no charts — that
// output now lands in Slides). The Ask-AI box sets both apps and they autorun
// (read-only aggregates).
import { memo, useCallback, useEffect, useState } from "react";
import { AggregateResult, ExploreDataset, ExploreFilters, exploreAggregate } from "../../lib/api";
import { SimpleColumn, SimpleTable } from "../../ui/SimpleTable";
import { AskBox } from "./AskBox";
import { FilterEditor, MetricSelect, Select, splittableDimensions } from "./controls";

type ChartType = "line" | "bar" | "stacked-bar";

interface ChartConfig {
  chartType: ChartType;
  metric: string;
  split: string | null;
  filters: ExploreFilters;
}

function rowsToObjects(res: AggregateResult): Record<string, unknown>[] {
  return res.rows.map((r) => Object.fromEntries(res.columns.map((c, i) => [c, r[i]])));
}

function defaultConfigs(dataset: ExploreDataset): ChartConfig[] {
  // Default splits avoid geo rollups and high-cardinality typeahead dims
  // (postcode/suburb) — stacking a bar by 500+ postcodes is unreadable. Users can
  // still pick those manually from the Split control.
  const splits = splittableDimensions(dataset).filter((d) => d.source !== "geo" && !d.typeahead);
  const firstSplit = splits[0]?.name ?? null;
  const secondSplit = splits.find((d) => d.name !== firstSplit)?.name ?? firstSplit;
  const countMetric =
    dataset.metrics.find((m) => m.kind === "additive" && m.format === "number")?.name ??
    dataset.metrics[0].name;
  return [
    { chartType: "line", metric: dataset.default_metric, split: firstSplit, filters: {} },
    { chartType: "stacked-bar", metric: countMetric, split: secondSplit, filters: {} },
  ];
}

export function TrendsTool({ dataset }: { dataset: ExploreDataset }) {
  const [configs, setConfigs] = useState<ChartConfig[]>(() => defaultConfigs(dataset));

  useEffect(() => {
    setConfigs(defaultConfigs(dataset));
  }, [dataset.slug]); // eslint-disable-line react-hooks/exhaustive-deps

  function applyAsk(state: Record<string, unknown>) {
    const charts = (state.charts as Record<string, unknown>[]) ?? [];
    setConfigs((prev) =>
      prev.map((cfg, i) => {
        const c = charts[i];
        if (!c) return cfg;
        const type = String(c.chart_type ?? cfg.chartType) as ChartType;
        return {
          chartType: ["line", "bar", "stacked-bar"].includes(type) ? type : cfg.chartType,
          metric: typeof c.metric === "string" ? c.metric : cfg.metric,
          split: (c.split as string | null) ?? null,
          filters: (c.filters as ExploreFilters) ?? {},
        };
      }),
    );
  }

  // Stable per-chart setters so the memoized ChartApp doesn't re-render (and
  // re-fetch/redraw) when the *other* chart's config changes.
  const setConfig0 = useCallback(
    (next: ChartConfig) => setConfigs((prev) => [next, prev[1]]),
    [],
  );
  const setConfig1 = useCallback(
    (next: ChartConfig) => setConfigs((prev) => [prev[0], next]),
    [],
  );

  return (
    <div className="ex-tool">
      <AskBox
        mode="trends"
        dataset={dataset.slug}
        placeholder='e.g. "avg rent by bedrooms as a line, and bond volume stacked by postcode"'
        onApply={applyAsk}
      />
      <div className="ex-grid2">
        <ChartApp index={0} dataset={dataset} config={configs[0]} onChange={setConfig0} />
        <ChartApp index={1} dataset={dataset} config={configs[1]} onChange={setConfig1} />
      </div>
    </div>
  );
}

const ChartApp = memo(function ChartApp({
  index,
  dataset,
  config,
  onChange,
}: {
  index: number;
  dataset: ExploreDataset;
  config: ChartConfig;
  onChange: (c: ChartConfig) => void;
}) {
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const splits = splittableDimensions(dataset).filter((d) => d.source !== "geo");
  const timeDim = dataset.time_dim;

  // Serialize the config so the fetch effect fires on any control change.
  const key = JSON.stringify(config);
  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    const group_by = [timeDim, ...(config.split ? [config.split] : [])];
    exploreAggregate({
      dataset: dataset.slug,
      metrics: [config.metric],
      group_by,
      filters: config.filters,
      limit: 3000,
    })
      .then((res) => {
        if (!live) return;
        setRows(rowsToObjects(res));
      })
      .catch((e) => live && setError((e as Error).message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [key, dataset.slug, timeDim]); // eslint-disable-line react-hooks/exhaustive-deps

  // The fetched rows as a plain table — no chart type/units, no ObjectBody:
  // the chart picker above still shapes the query (via group_by) but no longer
  // shapes a render (s46). Column order mirrors the group_by + metric shape.
  const tableColumns: SimpleColumn[] = [
    { key: timeDim, label: timeDim },
    ...(config.split ? [{ key: config.split, label: config.split }] : []),
    { key: config.metric, label: config.metric, align: "right" as const },
  ];

  return (
    <div className="ex-card ex-chartapp">
      <div className="ex-chartapp-head">trend {index + 1}</div>
      <div className="ex-ctrl-row">
        <Select
          label="Chart"
          value={config.chartType}
          onChange={(v) => onChange({ ...config, chartType: v as ChartType })}
          options={[
            { value: "line", label: "line" },
            { value: "bar", label: "bar" },
            { value: "stacked-bar", label: "stacked bar" },
          ]}
        />
        <MetricSelect
          dataset={dataset}
          value={config.metric}
          onChange={(v) => onChange({ ...config, metric: v })}
        />
        <Select
          label="Split"
          value={config.split ?? ""}
          onChange={(v) => onChange({ ...config, split: v || null })}
          options={[
            { value: "", label: "none" },
            ...splits.map((d) => ({ value: d.name, label: d.label })),
          ]}
        />
      </div>
      <FilterEditor
        dataset={dataset}
        filters={config.filters}
        onChange={(f) => onChange({ ...config, filters: f })}
      />
      {error ? (
        <p className="ex-error">{error}</p>
      ) : loading && rows.length === 0 ? (
        <div className="skel" style={{ height: 220 }} />
      ) : rows.length > 0 ? (
        <SimpleTable columns={tableColumns} rows={rows} max={100} />
      ) : (
        <p className="muted ex-hint">No rows for this selection.</p>
      )}
    </div>
  );
});
