// One assistant answer's payload: engine/rows meta, optional agent-trace
// expander (admin only), then the answer surface — an embedded Slides/Sheets
// artifact when the agent produced one, else the legacy SQL + rows affordances.
import { useState } from "react";
import { AskResult, goldenFromRun } from "../../lib/api";
import { downloadCsv } from "../../lib/csv";
import { AgentTrace, RunId, traceSummary } from "../../ui/AgentTrace";
import { ArtifactView } from "./ArtifactView";

export function ResultView({
  result,
  isAdmin,
  onOpenSql,
  onPromoteToGolden,
}: {
  result: AskResult;
  isAdmin: boolean;
  onOpenSql: (sql: string) => void;
  onPromoteToGolden?: (goldenId: string) => void;
}) {
  const [showTrace, setShowTrace] = useState(false);
  const [copied, setCopied] = useState(false);
  // ★ promote-to-golden: idle → saving → saved (then hands off to the Goldens
  // tab). Admin-only, and only when this answer has an audited run_id to copy.
  const [promoteState, setPromoteState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const canPromote = isAdmin && !!result.run_id && !!onPromoteToGolden;
  async function promote() {
    if (!result.run_id || promoteState === "saving") return;
    setPromoteState("saving");
    try {
      const res = await goldenFromRun(result.run_id);
      setPromoteState("saved");
      // Defer the handoff so "saved ✓" actually paints before the view-host
      // remounts into the Goldens tab (same-tick setState + navigate would
      // otherwise batch into one commit and skip painting this state).
      setTimeout(() => onPromoteToGolden?.(res.id), 600);
    } catch {
      setPromoteState("error");
    }
  }
  const hasTrace = isAdmin && result.steps.length > 0;
  // CSV source: the legacy rows, when present — an artifact answer's real data
  // lives in its Sheet (linked via "Open the data" below), not a CSV export.
  const csvSource = result.rows.length > 0 ? { columns: result.columns, rows: result.rows } : null;
  function copyAnswer() {
    void navigator.clipboard?.writeText(result.answer).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    });
  }
  if (!result.artifact && result.row_count === 0 && !result.sql) return null;
  const totalTokens = (result.input_tokens ?? 0) + (result.output_tokens ?? 0);
  const detailTitle = [
    `engine: ${result.engine}`,
    `${result.row_count} rows`,
    result.latency_ms != null ? `${(result.latency_ms / 1000).toFixed(1)}s` : null,
    totalTokens ? `${totalTokens.toLocaleString()} tokens` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className="result">
      <ArtifactView result={result} onOpenSql={onOpenSql} />
      {/* Answer-first (issue #10): the answer surface leads; the user actions come
          first, run internals sit quiet in a demoted hover-revealed cluster on the
          right and the full agent trace hides behind the "trace" expander. */}
      <div className="meta answer-meta">
        <span className="answer-actions">
          <button className="chip" onClick={copyAnswer} title="Copy the answer text">
            {copied ? "copied ✓" : "copy"}
          </button>
          {csvSource && (
            <button
              className="chip"
              title="Download the result rows as CSV"
              onClick={() =>
                downloadCsv(
                  csvSource.columns,
                  csvSource.rows,
                  `data-pilot-${result.run_id || "answer"}.csv`,
                )
              }
            >
              csv
            </button>
          )}
          {canPromote && (
            <button
              className="chip promote-golden"
              onClick={() => void promote()}
              disabled={promoteState === "saving" || promoteState === "saved"}
              title="Copy this answer into a draft golden — no re-run — and open it in the Golden Examples tab"
              data-testid="promote-golden"
            >
              {promoteState === "saving"
                ? "saving…"
                : promoteState === "saved"
                  ? "saved ✓ — opening in Goldens…"
                  : promoteState === "error"
                    ? "★ retry save as golden"
                    : "★ save as golden"}
            </button>
          )}
          {hasTrace && (
            <button className="link" onClick={() => setShowTrace((s) => !s)}>
              {showTrace ? "hide trace" : `trace · ${result.steps.length} steps`}
            </button>
          )}
        </span>
        <span className="answer-meta-details" title={detailTitle}>
          <span className={`badge ${result.engine}`}>{result.engine}</span>
          <span>{result.row_count} rows</span>
          {isAdmin && result.run_id && <RunId id={result.run_id} />}
        </span>
      </div>
      {showTrace && hasTrace && (
        <AgentTrace
          steps={result.steps}
          summary={traceSummary({
            engine: result.engine,
            steps: result.steps,
            latency_ms: result.latency_ms,
            input_tokens: result.input_tokens,
            output_tokens: result.output_tokens,
          })}
        />
      )}
    </div>
  );
}
