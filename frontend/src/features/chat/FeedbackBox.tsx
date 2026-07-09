// Element-pinned feedback: click any headline/insight/chart/query in a report
// to open this popover, rate it, and flag questionable numbers. Feeds the
// admin triage queue and the eval-case loop — element_ids must stay stable.
import { useEffect, useState } from "react";
import { InsightReport, submitFeedback } from "../../lib/api";

export interface Selected {
  kind: string;
  ref: string;
  label: string;
  snapshot: Record<string, unknown>;
  renderHtml: string;
  anchor: {
    top: number;
    left: number;
  };
}

export interface FeedbackMarker {
  rating: 1 | -1;
  accurate: boolean | null;
  issueFlag: boolean;
}

export function FeedbackMarkerIcon({ marker }: { marker: FeedbackMarker }) {
  return (
    <span
      className={`fb-marker${marker.issueFlag || marker.accurate === false ? " issue" : ""}`}
      title={
        marker.issueFlag || marker.accurate === false
          ? "Feedback left: number/question flagged"
          : "Feedback left"
      }
    >
      {marker.issueFlag || marker.accurate === false ? "!" : "💬"}
    </span>
  );
}

export function FeedbackBox({
  report,
  messageId,
  selected,
  marker,
  onSaved,
  onDone,
}: {
  report: InsightReport;
  messageId: string;
  selected: Selected | null;
  marker?: FeedbackMarker;
  onSaved: (ref: string, marker: FeedbackMarker) => void;
  onDone: () => void;
}) {
  const [rating, setRating] = useState<1 | -1 | null>(null);
  const [accurate, setAccurate] = useState<boolean | null>(null);
  const [issueFlag, setIssueFlag] = useState(false);
  const [comment, setComment] = useState("");

  useEffect(() => {
    if (!selected) return;
    setRating(marker?.rating ?? null);
    setAccurate(marker?.accurate ?? null);
    setIssueFlag(marker?.issueFlag ?? false);
    setComment("");
  }, [selected, marker]);

  if (!selected) {
    return (
      <p className="fb-cue">Click any headline, insight, chart or query above to leave feedback.</p>
    );
  }
  async function send() {
    if (!rating || accurate == null || !selected) return;
    try {
      await submitFeedback({
        message_id: messageId,
        rating,
        accurate,
        issue_flag: issueFlag,
        comment: comment || undefined,
        target_kind: selected.kind,
        target_ref: selected.ref,
        target_snapshot: selected.snapshot,
        target_render_html: selected.renderHtml,
        report_snapshot: report,
        knowledge_version: report.knowledge_version,
        knowledge_pages: report.knowledge_pages_used,
        client_context: {
          path: window.location.pathname,
          viewport: { width: window.innerWidth, height: window.innerHeight },
          user_agent: window.navigator.userAgent,
        },
      });
      onSaved(selected.ref, { rating, accurate, issueFlag });
      setRating(null);
      setAccurate(null);
      setIssueFlag(false);
      setComment("");
      onDone();
    } catch {
      /* surfaced by disabled state; keep it simple */
    }
  }
  return (
    <div
      className="fb-box pinned"
      style={{ top: selected.anchor.top, left: selected.anchor.left }}
    >
      <div className="fb-title">
        Feedback on <strong>{selected.kind}</strong> · “{selected.label}”
      </div>
      <div className="fb-form">
        <div className="fb-row">
          <span className="fb-label">Sentiment</span>
          <span className="fb-sent">
            <button className={rating === 1 ? "sel" : ""} onClick={() => setRating(1)}>
              👍 Useful
            </button>
            <button className={rating === -1 ? "sel" : ""} onClick={() => setRating(-1)}>
              👎 Off
            </button>
          </span>
        </div>
        <div className="fb-row">
          <span className="fb-label">Numbers</span>
          <span className="fb-sent">
            <button className={accurate === true ? "sel" : ""} onClick={() => setAccurate(true)}>
              accurate
            </button>
            <button
              className={accurate === false ? "sel warn" : ""}
              onClick={() => {
                setAccurate(false);
                setIssueFlag(true);
              }}
            >
              questionable
            </button>
            <button
              className={issueFlag ? "sel warn" : ""}
              onClick={() => {
                setIssueFlag((v) => !v);
                if (!issueFlag) setAccurate(false);
              }}
              title="Flag a questionable number in this element"
            >
              !
            </button>
          </span>
        </div>
        <textarea
          placeholder="What should the agent learn from this feedback?"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
        <div className="fb-actions">
          <button className="fb-submit" disabled={!rating || accurate == null} onClick={send}>
            Submit
          </button>
          <button
            className="chip"
            onClick={() => {
              setRating(null);
              setAccurate(null);
              setIssueFlag(false);
              setComment("");
              onDone();
            }}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
