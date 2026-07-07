// Feedback triage + eval-case management: promote feedback to evals, triage
// to user memory / dismiss, and run the staleness pass over eval cases.
import { Fragment, useState } from "react";
import {
  AdminFeedback,
  EvalCase,
  InsightReport,
  promoteFeedback,
  runEvalStaleness,
  setEvalCaseStatus,
  triageFeedback,
} from "../../lib/api";
import { formatTime, summarizeSnapshot } from "../../lib/format";
import { ReportPreview } from "./ReportPreview";

export function FeedbackAdmin({
  feedback,
  evalCases,
  onRefresh,
}: {
  feedback: AdminFeedback[];
  evalCases: EvalCase[];
  onRefresh: () => Promise<void>;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [ratingFilter, setRatingFilter] = useState("");

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setStatusMsg(null);
    try {
      const result = await fn();
      if (
        result &&
        typeof result === "object" &&
        "checked" in result &&
        "flagged_stale" in result &&
        "archived" in result
      ) {
        const r = result as { checked: number; flagged_stale: number; archived: number };
        setStatusMsg(
          `Staleness pass checked ${r.checked}; flagged ${r.flagged_stale}; archived ${r.archived}.`,
        );
      }
      await onRefresh();
      setSelected(new Set());
    } finally {
      setBusy(false);
    }
  }

  const newCount = feedback.filter((f) => f.status === "new").length;
  const filteredFeedback = feedback
    .filter((f) => !statusFilter || f.status === statusFilter)
    .filter((f) => !ratingFilter || String(f.rating) === ratingFilter);

  return (
    <>
      <section>
        <h3>Feedback ({newCount} new)</h3>
        <div className="fb-admin-actions">
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">All statuses</option>
            {[...new Set(feedback.map((f) => f.status))].sort().map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <select value={ratingFilter} onChange={(e) => setRatingFilter(e.target.value)}>
            <option value="">All ratings</option>
            <option value="1">Thumbs up</option>
            <option value="-1">Thumbs down</option>
          </select>
          <button
            className="chip"
            disabled={busy || selected.size === 0}
            onClick={() => run(() => promoteFeedback([...selected]))}
          >
            Promote selected to evals ({selected.size})
          </button>
          {statusMsg && <span className="muted">{statusMsg}</span>}
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th></th>
                <th>When</th>
                <th>User</th>
                <th>Rating</th>
                <th>Accuracy</th>
                <th>Element</th>
                <th>Question / comment</th>
                <th>Status</th>
                <th>Triage</th>
              </tr>
            </thead>
            <tbody>
              {filteredFeedback.map((f) => (
                <Fragment key={f.id}>
                  <tr>
                    <td>
                      {f.status === "new" && (
                        <input
                          type="checkbox"
                          checked={selected.has(f.id)}
                          onChange={() => toggle(f.id)}
                        />
                      )}
                    </td>
                    <td>{formatTime(f.created_at)}</td>
                    <td>{f.username}</td>
                    <td>{f.rating === 1 ? "👍" : "👎"}</td>
                    <td>
                      {f.issue_flag && <span className="issue-icon">!</span>}{" "}
                      {f.accurate == null ? "-" : f.accurate ? "accurate" : "questioned"}
                    </td>
                    <td>
                      <span className="badge">{f.target_kind}</span> {f.target_ref}
                    </td>
                    <td className="wide-cell">
                      <div className="fb-q">{f.question ?? "-"}</div>
                      {f.comment && <div className="fb-c">“{f.comment}”</div>}
                      <div className="fb-snap">{summarizeSnapshot(f.target_snapshot)}</div>
                      {f.target_render_html && (
                        <details className="fb-html">
                          <summary>rendered element HTML</summary>
                          <pre>{f.target_render_html}</pre>
                        </details>
                      )}
                      {(f.report_snapshot ?? f.report) && (
                        <button
                          className="link"
                          onClick={() => setPreviewId(previewId === f.id ? null : f.id)}
                        >
                          {previewId === f.id ? "hide report" : "review report"}
                        </button>
                      )}
                    </td>
                    <td>
                      <span className={`badge fb-status-${f.status}`}>{f.status}</span>
                    </td>
                    <td>
                      {f.status === "new" && (
                        <div className="fb-triage">
                          <button
                            className="link"
                            disabled={busy}
                            onClick={() => run(() => triageFeedback(f.id, "user_memory"))}
                          >
                            memory
                          </button>
                          <button
                            className="link"
                            disabled={busy}
                            onClick={() => run(() => triageFeedback(f.id, "dismiss"))}
                          >
                            dismiss
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                  {previewId === f.id && (f.report_snapshot ?? f.report) && (
                    <tr className="fb-preview-row">
                      <td colSpan={9}>
                        <div className="fb-preview-note">
                          Feedback pinned to <strong>{f.target_ref}</strong>
                          {f.comment ? `: "${f.comment}"` : ""}
                        </div>
                        <ReportPreview
                          report={(f.report_snapshot ?? f.report) as InsightReport}
                          selectedRef={f.target_ref}
                          selectedSnapshot={f.target_snapshot}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
              {filteredFeedback.length === 0 && (
                <tr>
                  <td colSpan={9} className="muted">
                    No feedback yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <div className="section-head">
          <h3>Eval cases ({evalCases.filter((c) => c.status === "active").length} active)</h3>
          <button className="chip" disabled={busy} onClick={() => run(runEvalStaleness)}>
            Run staleness pass
          </button>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Question</th>
                <th>Expectation</th>
                <th>Kind</th>
                <th>Knowledge</th>
                <th>Status</th>
                <th>Toggle</th>
              </tr>
            </thead>
            <tbody>
              {evalCases.map((c) => (
                <tr key={c.id}>
                  <td className="wide-cell">{c.question}</td>
                  <td className="wide-cell">{c.expectation}</td>
                  <td>{c.target_kind}</td>
                  <td title={c.knowledge_version}>{c.knowledge_version.slice(0, 7)}</td>
                  <td>
                    <span className={`badge fb-status-${c.status}`}>
                      {c.status}
                      {c.status === "stale" && c.stale_cycles > 0 ? ` (${c.stale_cycles})` : ""}
                    </span>
                  </td>
                  <td>
                    {c.status !== "archived" && (
                      <button
                        className="link"
                        disabled={busy}
                        onClick={() =>
                          run(() =>
                            setEvalCaseStatus(c.id, c.status === "active" ? "stale" : "active"),
                          )
                        }
                      >
                        {c.status === "active" ? "mark stale" : "mark active"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {evalCases.length === 0 && (
                <tr>
                  <td colSpan={6} className="muted">
                    No eval cases yet — promote feedback above to create some.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
