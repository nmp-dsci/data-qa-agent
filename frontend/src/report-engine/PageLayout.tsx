// PageLayout — THE page renderer for the s08 column model. Chat answers
// (PagesView), the Template Studio preview, and the playground all render
// through this one component, so the layouts can never fork again.
//
// Placement is positional: page.columns[i][j] renders in column i (left→right,
// grid tracks from the registry), slot j (top→bottom). Object meaning is typed
// (ObjectBody switches on o.type); `role` is a semantic label only. Objects may
// carry data.height — px or sm/md/lg (resolved sizes) or "fill" (the card
// stretches to match the tallest column, so a lone chart leaves no gap).
import { Fragment, lazy, ReactNode, Suspense } from "react";
import { Page, PageObject } from "../lib/api";
import { Bars, BarsData } from "../ui/charts/Bars";
import { Combo, ComboData } from "../ui/charts/Combo";
import { DataTable, TableData } from "../ui/charts/DataTable";
import { KPIData, KPITile } from "../ui/charts/KPITile";
import { Trend, TrendData } from "../ui/charts/Trend";
import { columnTracks, resolveHeight, templateFor } from "./registry";

// Choropleth carries the topojson + d3-geo, so it code-splits: only loads when a
// page actually renders a map object.
const Choropleth = lazy(() =>
  import("../ui/charts/Choropleth").then((m) => ({ default: m.Choropleth })),
);

export function ObjectBody({ o }: { o: PageObject }) {
  const d = o.data;
  switch (o.type) {
    case "kpi":
      return <KPITile data={d as unknown as KPIData} />;
    case "trend":
      return (
        <Trend
          height={resolveHeight(d["height"])}
          data={{
            x: String(d["x"] ?? "month"),
            y: String(d["y"] ?? "value"),
            series: (d["series"] as string | null) ?? null,
            title: (d["title"] as string | null) ?? null,
            sql: (d["sql"] as string | null) ?? null,
            rows: (d["rows"] as Record<string, unknown>[]) ?? [],
          } satisfies TrendData}
        />
      );
    case "breakdown":
    case "compare": {
      const barsData: BarsData = {
        dimension: String(d["dimension"] ?? ""),
        measure: String(d["measure"] ?? ""),
        group: (d["group"] as string | null) ?? null,
        title: (d["title"] as string | null) ?? null,
        stacked: Boolean(d["stacked"]),
        groupOrder: (d["group_order"] as string[] | undefined) ?? undefined,
        sortX: Boolean(d["sort_x"]),
        sql: (d["sql"] as string | null) ?? null,
        rows: (d["rows"] as Record<string, unknown>[]) ?? [],
      };
      // A `compare` carrying a second measure is a line+bar combo (dual axis):
      // grouped bars for `measure` + a per-series line for `line_measure`.
      if (o.type === "compare" && d["line_measure"]) {
        return (
          <Combo
            height={resolveHeight(d["height"])}
            data={{ ...barsData, line_measure: String(d["line_measure"]) } satisfies ComboData}
          />
        );
      }
      return <Bars height={resolveHeight(d["height"])} data={barsData} />;
    }
    case "table":
      return <DataTable data={d as unknown as TableData} />;
    case "choropleth":
      return (
        <Suspense fallback={<div className="skel" style={{ height: 220 }} />}>
          <Choropleth
            data={{
              layer: String(d["layer"] ?? "poa_nsw"),
              key_field: String(d["key_field"] ?? "postcode"),
              value_field: String(d["value_field"] ?? "value"),
              title: (d["title"] as string | null) ?? null,
              rows: (d["rows"] as Record<string, unknown>[]) ?? [],
              height: resolveHeight(d["height"]),
              diverging: Boolean(d["diverging"]),
            }}
          />
        </Suspense>
      );
    case "insight":
      return (
        <div className="i-body">
          {d["heading"] != null && String(d["heading"]) !== "" && (
            <div className="i-head">{String(d["heading"])}</div>
          )}
          {String(d["text"] ?? "")}{" "}
          {((d["refs"] as string[]) ?? []).map((q) => (
            <span key={q} className="i-ref">
              [{q}]
            </span>
          ))}
        </div>
      );
    case "text":
      return <div className="i-body">{String(d["text"] ?? "")}</div>;
    default:
      return null;
  }
}

/** Card class per object type; `fill` cards stretch to the column height. */
export function objectCardClass(o: PageObject): string {
  const base =
    o.type === "kpi"
      ? "h-tile page-obj"
      : o.type === "insight" || o.type === "text"
        ? "insight-card page-obj"
        : "chart-card page-obj";
  return o.data["height"] === "fill" ? `${base} fill` : base;
}

function DefaultCard({ o }: { o: PageObject }) {
  return (
    <div className={objectCardClass(o)} data-object-type={o.type}>
      <ObjectBody o={o} />
    </div>
  );
}

/** Chunk a column so runs of consecutive KPI tiles share a tile grid. */
function chunkColumn(col: PageObject[]): PageObject[][] {
  const chunks: PageObject[][] = [];
  for (const o of col) {
    const last = chunks[chunks.length - 1];
    if (o.type === "kpi" && last && last[0].type === "kpi") last.push(o);
    else chunks.push([o]);
  }
  return chunks;
}

export function PageLayout({
  page,
  renderObject,
}: {
  page: Page;
  /** Wraps each object (e.g. chat's feedback-pinned card). Defaults to a plain card. */
  renderObject?: (o: PageObject) => ReactNode;
}) {
  const template = templateFor(page);
  const kept = (page.columns ?? [])
    .map((col, index) => ({ col, index }))
    .filter((c) => c.col.length > 0)
    .slice(0, template.tracks.length);
  if (kept.length === 0) return null;
  // Authored pages may carry custom column widths — size those by the column's
  // ORIGINAL index (so a width set for column 2 still applies when column 1 is
  // empty). Without widths, keep the classic track-by-position fallback so live
  // agent answers render exactly as before.
  const trackList = columnTracks(page);
  const hasWidths = Array.isArray(page.widths) && page.widths.length > 0;
  const tracks = kept.map(({ index }, pos) =>
    hasWidths ? (trackList[index] ?? "minmax(0, 1fr)") : (template.tracks[pos] ?? "minmax(0, 1fr)"),
  );
  const render = renderObject ?? ((o: PageObject) => <DefaultCard key={o.element_id} o={o} />);

  return (
    <div
      className="page-cols"
      style={{ gridTemplateColumns: tracks.join(" ") }}
      data-template={page.template}
      data-col-count={kept.length}
    >
      {kept.map(({ col }, i) => (
        <div className="page-col" key={i} data-col={i}>
          {chunkColumn(col).map((chunk, j) =>
            chunk.length > 1 && chunk[0].type === "kpi" ? (
              <div className="tile-grid" key={j}>
                {chunk.map(render)}
              </div>
            ) : (
              <Fragment key={j}>{chunk.map(render)}</Fragment>
            ),
          )}
        </div>
      ))}
    </div>
  );
}
