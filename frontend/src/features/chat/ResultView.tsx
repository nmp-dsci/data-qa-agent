// One assistant answer's payload: engine/rows meta, optional agent-trace
// expander (admin only), then the insight report — or the legacy result
// (raw SQL + chart + rows) for pre-report answers.
import { useState } from "react";
import { AskResult } from "../../lib/api";
import { downloadCsv } from "../../lib/csv";
import { AgentTrace, RunId, traceSummary } from "../../ui/AgentTrace";
import { ContractJson } from "../../ui/ContractJson";
import { SpecChart } from "../../ui/SpecChart";
import { ReportView } from "./ReportView";
import { PagesView } from "../../report-engine/PagesView";

export function ResultView({
  result,
  isAdmin,
  onOpenSql,
}: {
  result: AskResult;
  isAdmin: boolean;
  onOpenSql: (sql: string) => void;
}) {
  const [showTrace, setShowTrace] = useState(false);
  const [showRenderJson, setShowRenderJson] = useState(false);
  const [copied, setCopied] = useState(false);
  const hasTrace = isAdmin && result.steps.length > 0;
  // The render contract (s10): the exact Page JSON the data-agent sent the
  // frontend — the same inspector Template Studio uses. Admin-only, like the trace.
  const renderPages = isAdmin && result.pages != null ? result.pages : [];
  const hasReport = result.report != null;
  // CSV source: legacy rows if present, else the report's first query with rows.
  const csvSource =
    result.rows.length > 0
      ? { columns: result.columns, rows: result.rows }
      : (() => {
          const q = result.report?.queries.find((qr) => qr.rows.length > 0);
          return q ? { columns: q.columns, rows: q.rows } : null;
        })();
  function copyAnswer() {
    void navigator.clipboard?.writeText(result.answer).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    });
  }
  if (!hasReport && result.row_count === 0 && !result.sql) return null;
  const totalTokens = (result.input_tokens ?? 0) + (result.output_tokens ?? 0);
  const detailTitle = [
    `engine: ${result.engine}`,
    `${result.row_count} rows`,
    result.latency_ms != null ? `${(result.latency_ms / 1000).toFixed(1)}s` : null,
    totalTokens ? `${totalTokens.toLocaleString()} tokens` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const body =
    result.report && result.pages && result.pages.length > 0 ? (
      <PagesView
        pages={result.pages}
        report={result.report}
        messageId={result.message_id}
        onOpenSql={onOpenSql}
      />
    ) : result.report ? (
      <ReportView report={result.report} messageId={result.message_id} onOpenSql={onOpenSql} />
    ) : (
      <LegacyResult result={result} />
    );
  return (
    <div className="result">
      {body}
      {/* Answer-first (issue #10): the report leads; the user actions come first,
          run internals sit quiet in a demoted hover-revealed cluster on the right
          and the full agent trace hides behind the "trace" expander. */}
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
          {hasTrace && (
            <button className="link" onClick={() => setShowTrace((s) => !s)}>
              {showTrace ? "hide trace" : `trace · ${result.steps.length} steps`}
            </button>
          )}
          {renderPages.length > 0 && (
            <button className="link" onClick={() => setShowRenderJson((s) => !s)}>
              {showRenderJson
                ? "hide render JSON"
                : `render JSON (${renderPages.length} page${renderPages.length === 1 ? "" : "s"})`}
            </button>
          )}
        </span>
        <span className="answer-meta-details" title={detailTitle}>
          <span className={`badge ${result.engine}`}>{result.engine}</span>
          <span>{result.row_count} rows</span>
          {result.report && (
            <span title="knowledge tree version that produced this report">
              knowledge @ {result.report.knowledge_version.slice(0, 7)}
            </span>
          )}
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
      {showRenderJson && renderPages.length > 0 && (
        <div className="render-json">
          {renderPages.map((page, i) => (
            <ContractJson
              key={i}
              page={page}
              testId={`render-json-${i}`}
              label={`Page ${i + 1} · ${page.template} — what data-agent sent the frontend to render this page`}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function LegacyResult({ result }: { result: AskResult }) {
  const [showSql, setShowSql] = useState(false);
  return (
    <>
      {result.sql && (
        <div className="meta">
          <button className="link" onClick={() => setShowSql((s) => !s)}>
            {showSql ? "hide SQL" : "show SQL"}
          </button>
        </div>
      )}
      {showSql && result.sql && <pre className="sql">{result.sql}</pre>}
      {result.chart && <SpecChart spec={result.chart} />}
      {result.rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {result.columns.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.slice(0, 25).map((row, ri) => (
                <tr key={ri}>
                  {row.map((cell, ci) => (
                    <td key={ci}>{String(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
