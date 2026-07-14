// PagesView — chat's report renderer: each page renders through the shared
// PageLayout (the same component Template Studio uses, so previews can never
// drift from real answers). Every object stays clickable for element-pinned
// feedback with its original element_id, so the feedback → evals loop is
// unchanged.
import { useState } from "react";
import { InsightReport, Page, PageObject } from "../lib/api";
import { FeedbackBox, FeedbackMarker, FeedbackMarkerIcon, Selected } from "../features/chat/FeedbackBox";
import { QueryRefCard } from "../features/chat/ReportView";
import { ObjectBody, objectCardClass, PageLayout } from "./PageLayout";
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

/** A compact snapshot (no bulky rows) stored with feedback. */
function summarizeData(o: PageObject): Record<string, unknown> {
  const { rows, series, ...rest } = o.data;
  const rowCount = Array.isArray(rows) ? rows.length : undefined;
  void series;
  return { ...rest, row_count: rowCount };
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
  return (
    <div
      className={`${objectCardClass(o)}${selected?.ref === o.element_id ? " sel" : ""}`}
      data-object-type={o.type}
      onClick={(e) =>
        onPick(
          o.type,
          o.element_id,
          objectLabel(o),
          { type: o.type, role: o.role ?? null, ...summarizeData(o) },
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
        <section className="answer-page" key={i}>
          <p className="report-sec">
            Page {i + 1} · {templateFor(page).label}
          </p>
          {page.headline && <p className="page-headline">{page.headline}</p>}
          <PageLayout
            page={page}
            renderObject={(o) => (
              <PageObjectCard
                key={o.element_id}
                o={o}
                selected={selected}
                marker={markers[o.element_id]}
                onPick={pick}
              />
            )}
          />
        </section>
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
