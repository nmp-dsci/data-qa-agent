// PageLayout — THE page renderer for the s08 column model. Chat answers
// (PagesView), the Template Studio preview, and the playground all render
// through this one component, so the layouts can never fork again.
//
// Placement is positional: page.columns[i][j] renders in column i (left→right,
// grid tracks from the registry), slot j (top→bottom). Object meaning is typed
// (ObjectBody switches on o.type); `role` is a semantic label only. Objects may
// carry data.height — px or sm/md/lg (resolved sizes) or "fill" (the card
// stretches to match the tallest column, so a lone chart leaves no gap).
import { Fragment, ReactNode } from "react";
import { Page, PageObject } from "../lib/api";
import { Bars, BarsData } from "../ui/charts/Bars";
import { KPIData, KPITile } from "../ui/charts/KPITile";
import { Trend, TrendData } from "../ui/charts/Trend";
import { resolveHeight, templateFor } from "./registry";

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
            rows: (d["rows"] as Record<string, unknown>[]) ?? [],
          } satisfies TrendData}
        />
      );
    case "breakdown":
    case "compare":
      return (
        <Bars
          height={resolveHeight(d["height"])}
          data={{
            dimension: String(d["dimension"] ?? ""),
            measure: String(d["measure"] ?? ""),
            group: (d["group"] as string | null) ?? null,
            title: (d["title"] as string | null) ?? null,
            rows: (d["rows"] as Record<string, unknown>[]) ?? [],
          } satisfies BarsData}
        />
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
  const cols = (page.columns ?? []).filter((c) => c.length > 0).slice(0, template.tracks.length);
  if (cols.length === 0) return null;
  const tracks = cols.map((_, i) => template.tracks[i] ?? "minmax(0, 1fr)");
  const render = renderObject ?? ((o: PageObject) => <DefaultCard key={o.element_id} o={o} />);

  return (
    <div
      className="page-cols"
      style={{ gridTemplateColumns: tracks.join(" ") }}
      data-template={page.template}
      data-col-count={cols.length}
    >
      {cols.map((col, i) => (
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
