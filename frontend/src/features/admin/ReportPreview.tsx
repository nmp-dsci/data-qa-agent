// Read-only report snapshot preview used by the feedback triage table —
// shows the stored report with the feedback's target element highlighted.
import { InsightReport } from "../../lib/api";
import { summarizeSnapshot } from "../../lib/format";

export function ReportPreview({
  report,
  selectedRef,
  selectedSnapshot,
}: {
  report: InsightReport;
  selectedRef: string;
  selectedSnapshot: Record<string, unknown>;
}) {
  return (
    <div className="report admin-report-preview">
      {report.headlines.length > 0 && (
        <>
          <p className="report-sec">Headlines</p>
          <div className="headline-grid">
            {report.headlines.map((h) => (
              <div
                key={h.element_id}
                className={`h-tile${h.related ? " related" : ""}${
                  h.element_id === selectedRef ? " sel" : ""
                }`}
              >
                <div className="h-label">{h.label}</div>
                <div className="h-value">{h.value}</div>
                {h.basis && <div className="h-basis">{h.basis}</div>}
              </div>
            ))}
          </div>
        </>
      )}
      {report.insights.length > 0 && (
        <>
          <p className="report-sec">Insights</p>
          <div className="insight-list">
            {report.insights.map((ins) => (
              <div
                key={ins.element_id}
                className={`insight-card${ins.element_id === selectedRef ? " sel" : ""}`}
              >
                <div className="i-head">{ins.heading}</div>
                <div className="i-body">{ins.body}</div>
              </div>
            ))}
          </div>
        </>
      )}
      {report.profiles.map((p) => (
        <div key={p.element_id}>
          <p className="report-sec">{p.heading}</p>
          <div className={`profile-card${p.element_id === selectedRef ? " sel" : ""}`}>
            <div className="i-body">{p.body}</div>
          </div>
        </div>
      ))}
      {report.queries.length > 0 && (
        <>
          <p className="report-sec">Query references</p>
          {report.queries.map((q) => (
            <div key={q.element_id} className={`qref${q.element_id === selectedRef ? " sel" : ""}`}>
              <div className="qref-bar">
                <span className="qtag">{q.ref}</span>
                <span>
                  {q.purpose || "query"} · {q.row_count} rows
                </span>
              </div>
            </div>
          ))}
        </>
      )}
      {!report.headlines.some((h) => h.element_id === selectedRef) &&
        !report.insights.some((i) => i.element_id === selectedRef) &&
        !report.profiles.some((p) => p.element_id === selectedRef) &&
        !report.queries.some((q) => q.element_id === selectedRef) && (
          <div className="fb-preview-note">
            Stored snapshot: {summarizeSnapshot(selectedSnapshot)}
          </div>
        )}
    </div>
  );
}
