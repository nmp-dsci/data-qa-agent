// DictionaryTool — the data dictionary (rendered from the manifest) plus an
// extract builder: pick group-by dimensions and metrics, preview, download CSV.
// Both tables render as typed `table` page objects through the report engine's
// ObjectBody (s20) — the same path chat answers and goldens use.
import { useState } from "react";
import { AggregateResult, ExploreDataset, exploreAggregate, PageObject } from "../../lib/api";
import { downloadCsv } from "../../lib/csv";
import { ObjectBody } from "../../report-engine/PageLayout";
import { splittableDimensions } from "./controls";

function tableObject(element_id: string, data: PageObject["data"]): PageObject {
  return { type: "table", element_id, role: "table", data };
}

export function DictionaryTool({ dataset }: { dataset: ExploreDataset }) {
  const [groupBy, setGroupBy] = useState<string[]>([]);
  const [metrics, setMetrics] = useState<string[]>([dataset.default_metric]);
  const [result, setResult] = useState<AggregateResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const dictRows = [
    ...dataset.dimensions.map((d) => ({
      column: d.name,
      role: `dimension · ${d.kind}`,
      values: d.typeahead
        ? "typeahead"
        : d.kind === "time"
          ? `${dataset.time_range?.min ?? ""} → ${dataset.time_range?.max ?? ""}`
          : (d.domain ?? [])
              .slice(0, 8)
              .map((x) => String(x.value))
              .join(" · ") + ((d.domain?.length ?? 0) > 8 ? " …" : ""),
    })),
    ...dataset.metrics.map((m) => ({
      column: m.name,
      role: `metric · ${m.kind}`,
      values: m.format,
    })),
  ];

  function toggle(list: string[], v: string, cap = 99): string[] {
    if (list.includes(v)) return list.filter((x) => x !== v);
    if (list.length >= cap) return list;
    return [...list, v];
  }

  async function run() {
    if (metrics.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      const res = await exploreAggregate({
        dataset: dataset.slug,
        metrics,
        group_by: groupBy,
        limit: 50000,
      });
      setResult(res);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  function download() {
    if (result) downloadCsv(result.columns, result.rows as unknown[][], `${dataset.slug}_extract.csv`);
  }

  const previewCols = result?.columns ?? [];
  const previewRows = (result?.rows ?? []).slice(0, 50).map((r) =>
    Object.fromEntries(previewCols.map((c, i) => [c, r[i]])),
  );

  return (
    <div className="ex-tool ex-grid2">
      <div className="ex-card" data-object-type="table">
        <ObjectBody
          o={tableObject("explore:dictionary", {
            title: "Columns & values · from the manifest",
            variant: "plain",
            columns: [
              { key: "column", label: "Column" },
              { key: "role", label: "Role" },
              { key: "values", label: "Values / range" },
            ],
            rows: dictRows,
          })}
        />
      </div>

      <div className="ex-card">
        <h4>Extract builder</h4>
        <div className="ex-pick">
          <div className="ex-pick-label">Group by (max 3)</div>
          <div className="ex-pick-opts">
            {splittableDimensions(dataset, { includeTime: true })
              .filter((d) => !d.typeahead)
              .map((d) => (
                <button
                  key={d.name}
                  className={`ex-toggle${groupBy.includes(d.name) ? " on" : ""}`}
                  onClick={() => setGroupBy((g) => toggle(g, d.name, 3))}
                >
                  {d.label}
                </button>
              ))}
          </div>
        </div>
        <div className="ex-pick">
          <div className="ex-pick-label">Metrics</div>
          <div className="ex-pick-opts">
            {dataset.metrics.map((m) => (
              <button
                key={m.name}
                className={`ex-toggle${metrics.includes(m.name) ? " on" : ""}`}
                onClick={() => setMetrics((s) => toggle(s, m.name))}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>
        <div className="ex-pick-actions">
          <button className="ex-run" onClick={run} disabled={loading || metrics.length === 0}>
            {loading ? "Running…" : "Preview"}
          </button>
          <button className="ex-secondary" onClick={download} disabled={!result}>
            ⬇ Download CSV
          </button>
          {result && (
            <span className="muted">
              {result.row_count.toLocaleString()} rows{result.truncated ? " (capped)" : ""}
            </span>
          )}
        </div>
        {error && <p className="ex-error">{error}</p>}
        {result && previewRows.length > 0 && (
          <div data-object-type="table">
            <ObjectBody
              o={tableObject("explore:extract-preview", {
                variant: "plain",
                columns: previewCols.map((c) => ({ key: c, label: c })),
                rows: previewRows,
              })}
            />
          </div>
        )}
      </div>
    </div>
  );
}
