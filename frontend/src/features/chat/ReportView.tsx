// Insight-report renderer: headlines, insights, profiles, main chart and
// query refs — every element clickable for pinned feedback (element_id-keyed).
import { useRef, useState } from "react";
import { Headline, InsightReport, QueryRef } from "../../lib/api";
import { VegaChart } from "../../ui/VegaChart";
import { FeedbackBox, FeedbackMarker, FeedbackMarkerIcon, Selected } from "./FeedbackBox";

export function ReportView({
  report,
  messageId,
  onOpenSql,
}: {
  report: InsightReport;
  messageId: string;
  onOpenSql: (sql: string) => void;
}) {
  const reportRef = useRef<HTMLDivElement | null>(null);
  const [selected, setSelected] = useState<Selected | null>(null);
  const [feedbackMarkers, setFeedbackMarkers] = useState<Record<string, FeedbackMarker>>({});
  const primary = report.headlines.filter((h) => !h.related);
  const related = report.headlines.filter((h) => h.related);

  function pick(
    kind: string,
    ref: string,
    label: string,
    snapshot: Record<string, unknown>,
    renderHtml: string,
    anchorEl: HTMLElement,
  ) {
    const anchorRect = anchorEl.getBoundingClientRect();
    const popoverWidth = 320;
    const popoverHeight = 260;
    const viewportGutter = 18;
    const preferredLeft = anchorRect.right + 12;
    const maxLeft = window.innerWidth - popoverWidth - viewportGutter;
    const maxTop = window.innerHeight - popoverHeight - viewportGutter;
    const anchor = {
      top: Math.max(viewportGutter, Math.min(anchorRect.top, maxTop)),
      left: Math.max(viewportGutter, Math.min(preferredLeft, maxLeft)),
    };
    setSelected({ kind, ref, label, snapshot, renderHtml, anchor });
  }

  function markFeedback(ref: string, marker: FeedbackMarker) {
    setFeedbackMarkers((prev) => ({ ...prev, [ref]: marker }));
  }

  return (
    <div className="report" ref={reportRef}>
      {report.headlines.length > 0 && (
        <>
          <p className="report-sec">Headlines</p>
          <div className="headline-grid">
            {primary.map((h) => (
              <HeadlineTile
                key={h.element_id}
                h={h}
                selected={selected}
                marker={feedbackMarkers[h.element_id]}
                onPick={pick}
              />
            ))}
          </div>
          {related.length > 0 && (
            <>
              <div className="headline-grid">
                {related.map((h) => (
                  <HeadlineTile
                    key={h.element_id}
                    h={h}
                    selected={selected}
                    marker={feedbackMarkers[h.element_id]}
                    onPick={pick}
                  />
                ))}
              </div>
              <p className="related-hint">
                Related context metrics (not directly asked) — shown for comparison.
              </p>
            </>
          )}
        </>
      )}

      {report.insights.length > 0 && (
        <>
          <p className="report-sec">Insights</p>
          <div className="insight-list">
            {report.insights.map((ins) => (
              <div
                key={ins.element_id}
                className={`insight-card${selected?.ref === ins.element_id ? " sel" : ""}`}
                onClick={(e) =>
                  pick(
                    "insight",
                    ins.element_id,
                    ins.heading,
                    {
                      heading: ins.heading,
                      body: ins.body,
                      query_refs: ins.query_refs,
                    },
                    e.currentTarget.outerHTML,
                    e.currentTarget,
                  )
                }
              >
                {feedbackMarkers[ins.element_id] && (
                  <FeedbackMarkerIcon marker={feedbackMarkers[ins.element_id]} />
                )}
                <div className="i-head">{ins.heading}</div>
                <div className="i-body">
                  {ins.body}{" "}
                  {ins.query_refs.map((q) => (
                    <span key={q} className="i-ref">
                      [{q}]
                    </span>
                  ))}
                </div>
                {ins.chart && <VegaChart spec={ins.chart} />}
              </div>
            ))}
          </div>
        </>
      )}

      {report.profiles.map((p) => (
        <div key={p.element_id}>
          <p className="report-sec">{p.heading}</p>
          <div
            className={`profile-card${selected?.ref === p.element_id ? " sel" : ""}`}
            onClick={(e) =>
              pick(
                "profile",
                p.element_id,
                p.heading,
                { heading: p.heading, body: p.body },
                e.currentTarget.outerHTML,
                e.currentTarget,
              )
            }
          >
            {feedbackMarkers[p.element_id] && (
              <FeedbackMarkerIcon marker={feedbackMarkers[p.element_id]} />
            )}
            <div className="i-body">
              {p.body}{" "}
              {p.query_refs.map((q) => (
                <span key={q} className="i-ref">
                  [{q}]
                </span>
              ))}
            </div>
            {p.chart && <VegaChart spec={p.chart} />}
          </div>
        </div>
      ))}

      {report.main_chart && (
        <>
          <p className="report-sec">Trend</p>
          <div
            className={`chart-card${selected?.ref === "report:chart" ? " sel" : ""}`}
            onClick={(e) =>
              pick(
                "chart",
                "report:chart",
                "Main chart",
                {},
                e.currentTarget.outerHTML,
                e.currentTarget,
              )
            }
          >
            {feedbackMarkers["report:chart"] && (
              <FeedbackMarkerIcon marker={feedbackMarkers["report:chart"]} />
            )}
            <VegaChart spec={report.main_chart} />
          </div>
        </>
      )}

      {report.queries.length > 0 && (
        <>
          <p className="report-sec">Query references</p>
          {report.queries.map((q) => (
            <QueryRefCard
              key={q.element_id}
              q={q}
              selected={selected}
              marker={feedbackMarkers[q.element_id]}
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
        marker={selected ? feedbackMarkers[selected.ref] : undefined}
        onSaved={markFeedback}
        onDone={() => setSelected(null)}
      />
    </div>
  );
}

function HeadlineTile({
  h,
  selected,
  marker,
  onPick,
}: {
  h: Headline;
  selected: Selected | null;
  marker?: FeedbackMarker;
  onPick: (
    kind: string,
    ref: string,
    label: string,
    snap: Record<string, unknown>,
    renderHtml: string,
    anchorEl: HTMLElement,
  ) => void;
}) {
  return (
    <div
      className={`h-tile${h.related ? " related" : ""}${selected?.ref === h.element_id ? " sel" : ""}`}
      onClick={(e) =>
        onPick(
          "headline",
          h.element_id,
          h.label,
          { label: h.label, value: h.value, basis: h.basis },
          e.currentTarget.outerHTML,
          e.currentTarget,
        )
      }
    >
      {marker && <FeedbackMarkerIcon marker={marker} />}
      <div className="h-label">{h.label}</div>
      <div className="h-value">{h.value}</div>
      {h.basis && <div className="h-basis">{h.basis}</div>}
    </div>
  );
}

function QueryRefCard({
  q,
  selected,
  marker,
  onPick,
  onOpenSql,
}: {
  q: QueryRef;
  selected: Selected | null;
  marker?: FeedbackMarker;
  onPick: (
    kind: string,
    ref: string,
    label: string,
    snap: Record<string, unknown>,
    renderHtml: string,
    anchorEl: HTMLElement,
  ) => void;
  onOpenSql: (sql: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <div className={`qref${selected?.ref === q.element_id ? " sel" : ""}`}>
      <div
        className="qref-bar"
        onClick={(e) =>
          onPick(
            "query",
            q.element_id,
            q.ref,
            {
              ref: q.ref,
              purpose: q.purpose,
              sql: q.sql,
              row_count: q.row_count,
            },
            e.currentTarget.parentElement?.outerHTML ?? e.currentTarget.outerHTML,
            e.currentTarget.parentElement ?? e.currentTarget,
          )
        }
      >
        {marker && <FeedbackMarkerIcon marker={marker} />}
        <span className="qtag">{q.ref}</span>
        <span>
          {q.purpose || "query"} · {q.row_count} rows
        </span>
        <span className="qref-actions">
          {q.sql && (
            <button
              className="chip"
              onClick={(e) => {
                e.stopPropagation();
                navigator.clipboard?.writeText(q.sql ?? "");
                setCopied(true);
                setTimeout(() => setCopied(false), 1200);
              }}
            >
              {copied ? "copied" : "Copy SQL"}
            </button>
          )}
          {q.sql && (
            <button
              className="chip"
              onClick={(e) => {
                e.stopPropagation();
                onOpenSql(q.sql ?? "");
              }}
            >
              Open in SQL editor
            </button>
          )}
        </span>
      </div>
      {q.sql && <pre className="sql">{q.sql}</pre>}
    </div>
  );
}
