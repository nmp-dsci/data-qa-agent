// PagesView — renders the agent's pages contract: each page resolves its
// template from the registry, objects render by type (visx charts, KPI tiles,
// insight notes) placed by region. Every object stays clickable for
// element-pinned feedback with its original element_id, so the feedback →
// evals loop is unchanged from the classic report renderer.
import { useState } from "react";
import { InsightReport, Page, PageObject } from "../lib/api";
import { Bars, BarsData } from "../ui/charts/Bars";
import { KPIData, KPITile } from "../ui/charts/KPITile";
import { Trend, TrendData } from "../ui/charts/Trend";
import { FeedbackBox, FeedbackMarker, FeedbackMarkerIcon, Selected } from "../features/chat/FeedbackBox";
import { QueryRefCard } from "../features/chat/ReportView";
import { templateFor } from "./registry";

type PickFn = (
  kind: string,
  ref: string,
  label: string,
  snapshot: Record<string, unknown>,
  renderHtml: string,
  anchorEl: HTMLElement,
) => void;

function objectLabel(o: PageObject): string {
  const d = o.data;
  return String(d["label"] ?? d["title"] ?? d["heading"] ?? o.type);
}

function ObjectBody({ o }: { o: PageObject }) {
  const d = o.data;
  switch (o.type) {
    case "kpi":
      return <KPITile data={d as unknown as KPIData} />;
    case "trend":
      return (
        <Trend
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

function PageObjectCard({
  o,
  selected,
  marker,
  onPick,
}: {
  o: PageObject;
  selected: Selected | null;
  marker?: FeedbackMarker;
  onPick: PickFn;
}) {
  const cls =
    o.type === "kpi"
      ? "h-tile page-obj"
      : o.type === "insight" || o.type === "text"
        ? "insight-card page-obj"
        : "chart-card page-obj";
  return (
    <div
      className={`${cls}${selected?.ref === o.element_id ? " sel" : ""}`}
      onClick={(e) =>
        onPick(
          o.type,
          o.element_id,
          objectLabel(o),
          { type: o.type, region: o.region, ...summarizeData(o) },
          e.currentTarget.outerHTML,
          e.currentTarget,
        )
      }
    >
      {marker && <FeedbackMarkerIcon marker={marker} />}
      <ObjectBody o={o} />
    </div>
  );
}

/** A compact snapshot (no bulky rows) stored with feedback. */
function summarizeData(o: PageObject): Record<string, unknown> {
  const { rows, series, ...rest } = o.data;
  const rowCount = Array.isArray(rows) ? rows.length : undefined;
  void series;
  return { ...rest, row_count: rowCount };
}

function PageSection({
  page,
  index,
  selected,
  markers,
  onPick,
}: {
  page: Page;
  index: number;
  selected: Selected | null;
  markers: Record<string, FeedbackMarker>;
  onPick: PickFn;
}) {
  const template = templateFor(page);
  const byRegion = new Map<string, PageObject[]>();
  for (const o of page.objects) {
    const arr = byRegion.get(o.region) ?? [];
    arr.push(o);
    byRegion.set(o.region, arr);
  }
  const orderedRegions = [
    ...template.regions.filter((r) => byRegion.has(r)),
    ...[...byRegion.keys()].filter((r) => !template.regions.includes(r)),
  ];

  const renderRegion = (region: string) => {
    const objs = byRegion.get(region) ?? [];
    const isHero = objs.every((o) => o.type === "kpi");
    return (
      <div key={region} className={isHero ? "headline-grid" : "page-region"}>
        {objs.map((o) => (
          <PageObjectCard
            key={o.element_id}
            o={o}
            selected={selected}
            marker={markers[o.element_id]}
            onPick={onPick}
          />
        ))}
      </div>
    );
  };

  const chartRegions = orderedRegions.filter((r) => r === "chart");
  const otherRegions = orderedRegions.filter((r) => r !== "chart");

  return (
    <section className="answer-page">
      <p className="report-sec">
        Page {index + 1} · {template.label}
      </p>
      {template.layout === "two-col" && chartRegions.length > 0 ? (
        <div className="page-two-col">
          <div>{chartRegions.map(renderRegion)}</div>
          <div>{otherRegions.map(renderRegion)}</div>
        </div>
      ) : (
        orderedRegions.map(renderRegion)
      )}
    </section>
  );
}

export function PagesView({
  pages,
  report,
  messageId,
  onOpenSql,
}: {
  pages: Page[];
  report: InsightReport;
  messageId: string;
  onOpenSql: (sql: string) => void;
}) {
  const [selected, setSelected] = useState<Selected | null>(null);
  const [markers, setMarkers] = useState<Record<string, FeedbackMarker>>({});

  const pick: PickFn = (kind, ref, label, snapshot, renderHtml, anchorEl) => {
    const anchorRect = anchorEl.getBoundingClientRect();
    const popoverWidth = 320;
    const popoverHeight = 260;
    const gutter = 18;
    const anchor = {
      top: Math.max(gutter, Math.min(anchorRect.top, window.innerHeight - popoverHeight - gutter)),
      left: Math.max(
        gutter,
        Math.min(anchorRect.right + 12, window.innerWidth - popoverWidth - gutter),
      ),
    };
    setSelected({ kind, ref, label, snapshot, renderHtml, anchor });
  };

  return (
    <div className="report">
      {pages.map((page, i) => (
        <PageSection
          key={i}
          page={page}
          index={i}
          selected={selected}
          markers={markers}
          onPick={pick}
        />
      ))}

      {report.queries.length > 0 && (
        <>
          <p className="report-sec">Query references</p>
          {report.queries.map((q) => (
            <QueryRefCard
              key={q.element_id}
              q={q}
              selected={selected}
              marker={markers[q.element_id]}
              onPick={pick}
              onOpenSql={onOpenSql}
            />
          ))}
        </>
      )}

      {report.knowledge_pages_used.length > 0 && (
        <p className="report-foot">planned with: {report.knowledge_pages_used.join(" · ")}</p>
      )}

      <FeedbackBox
        report={report}
        messageId={messageId}
        selected={selected}
        marker={selected ? markers[selected.ref] : undefined}
        onSaved={(ref, marker) => setMarkers((prev) => ({ ...prev, [ref]: marker }))}
        onDone={() => setSelected(null)}
      />
    </div>
  );
}
