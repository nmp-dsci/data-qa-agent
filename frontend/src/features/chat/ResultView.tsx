// One assistant answer's payload: engine/rows meta, optional agent-trace
// expander (admin only), then the insight report — or the legacy result
// (raw SQL + chart + rows) for pre-report answers.
import { useState } from "react";
import { AskResult } from "../../lib/api";
import { AgentTrace, RunId, traceSummary } from "../../ui/AgentTrace";
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
  const hasTrace = isAdmin && result.steps.length > 0;
  const hasReport = result.report != null;
  if (!hasReport && result.row_count === 0 && !result.sql) return null;
  return (
    <div className="result">
      <div className="meta">
        <span className={`badge ${result.engine}`}>{result.engine}</span>
        <span>{result.row_count} rows</span>
        {result.report && (
          <span className="muted" title="knowledge tree version that produced this report">
            knowledge @ {result.report.knowledge_version.slice(0, 7)}
          </span>
        )}
        {isAdmin && result.run_id && <RunId id={result.run_id} />}
        {hasTrace && (
          <button className="link" onClick={() => setShowTrace((s) => !s)}>
            {showTrace ? "hide agent run" : `agent run (${result.steps.length} steps)`}
          </button>
        )}
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
      {result.report && result.pages && result.pages.length > 0 ? (
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
