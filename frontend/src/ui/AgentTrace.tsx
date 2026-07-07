// Agent run trace — shared by the chat result expander and the admin
// query-runs table. Renders message-history steps (system/user/model/
// tool_return/retry) plus legacy hand-built kinds from old stored traces.
import { useState } from "react";
import type { AgentStep } from "../lib/api";
import { fmtTokens } from "../lib/format";

export function RunId({ id }: { id: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="run-id"
      title={`Copy run id ${id} — hand it to Claude / inspect_run.py to diagnose this question`}
      onClick={() => {
        navigator.clipboard?.writeText(id);
        setCopied(true);
        setTimeout(() => setCopied(false), 1200);
      }}
    >
      {copied ? "copied ✓" : `run ${id.slice(0, 8)}`}
    </button>
  );
}

const STEP_LABELS: Record<string, string> = {
  system: "System prompt",
  user: "User question",
  model: "Model",
  tool_return: "Tool result",
  retry: "Retry",
  sql: "SQL",
  chart: "Chart",
  memory: "Remembered preference",
  analytics: "Analytics",
  knowledge: "Knowledge",
  decision_log: "Decision Log",
};

function stepLabel(s: AgentStep): string {
  if (s.kind === "model") return s.model_name ? `Model · ${s.model_name}` : "Model";
  if (s.kind === "tool_return") return `Tool result · ${s.name ?? ""}`.trim();
  if (s.kind === "retry") return s.name ? `Retry · ${s.name}` : "Retry";
  // Legacy hand-built kinds.
  if (s.kind === "sql") return `SQL attempt ${s.attempt ?? ""}`.trim();
  if (s.kind === "chart") return `Chart${s.mark ? ` (${s.mark})` : ""}`;
  return STEP_LABELS[s.kind] ?? s.kind;
}

export function traceSummary(opts: {
  engine: string;
  steps: AgentStep[];
  latency_ms?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
}): string {
  const modelTurns = opts.steps.filter((s) => s.kind === "model").length;
  const toolCalls = opts.steps.reduce((n, s) => n + (s.tool_calls?.length ?? 0), 0);
  const legacySql = opts.steps.filter((s) => s.kind === "sql").length;
  const sqlCalls =
    opts.steps.reduce(
      (n, s) => n + (s.tool_calls?.filter((t) => t.name === "run_sql").length ?? 0),
      0,
    ) || legacySql;
  const parts = [opts.engine];
  if (modelTurns) parts.push(`${modelTurns} model turn${modelTurns === 1 ? "" : "s"}`);
  if (toolCalls) parts.push(`${toolCalls} tool call${toolCalls === 1 ? "" : "s"}`);
  if (sqlCalls) parts.push(`${sqlCalls} SQL`);
  if (opts.input_tokens != null && opts.output_tokens != null) {
    parts.push(`${fmtTokens(opts.input_tokens)}/${fmtTokens(opts.output_tokens)} tok`);
  }
  if (opts.latency_ms != null) parts.push(`${opts.latency_ms} ms`);
  return parts.join(" · ");
}

function TracePayload({ text, previewLen = 240 }: { text: string; previewLen?: number }) {
  if (!text) return null;
  if (text.length <= previewLen) return <pre className="trace-pre">{text}</pre>;
  return (
    <details className="trace-collapse">
      <summary>
        <span className="trace-preview">{text.slice(0, previewLen)}…</span>
        <span className="muted"> ({text.length.toLocaleString()} chars)</span>
      </summary>
      <pre className="trace-pre">{text}</pre>
    </details>
  );
}

export function AgentTrace({ steps, summary }: { steps: AgentStep[]; summary?: string }) {
  return (
    <div className="agent-trace">
      {summary && <div className="trace-summary">{summary}</div>}
      {steps.length === 0 && <div className="muted">No steps recorded for this run.</div>}
      <ol className="trace-steps">
        {steps.map((s, i) => (
          <li key={i} className={`trace-step kind-${s.kind}`}>
            <div className="trace-head">
              <span className="trace-num">{i + 1}</span>
              <span className="trace-kind">{stepLabel(s)}</span>
              {s.kind === "model" && (s.input_tokens != null || s.output_tokens != null) && (
                <span className="trace-tokens" title="prompt / completion tokens for this turn">
                  {fmtTokens(s.input_tokens)} in · {fmtTokens(s.output_tokens)} out
                </span>
              )}
              {s.status && <span className={`trace-badge ${s.status}`}>{s.status}</span>}
              {s.row_count != null && <span className="trace-meta">{s.row_count} rows</span>}
              {s.intent && <span className="trace-meta">intent: {s.intent}</span>}
            </div>

            {/* Message-history steps */}
            {s.thinking && (
              <details className="trace-collapse">
                <summary>reasoning</summary>
                <pre className="trace-pre">{s.thinking}</pre>
              </details>
            )}
            {s.content && <TracePayload text={s.content} />}
            {s.tool_calls?.map((t, ti) => (
              <div key={ti} className="trace-toolcall">
                <div className="trace-sub">
                  calls <strong>{t.name}</strong>
                </div>
                <TracePayload text={t.args} />
              </div>
            ))}

            {s.decisions && s.decisions.length > 0 && (
              <ol className="decision-list">
                {s.decisions.map((d, di) => (
                  <li key={di} className={`decision-item decision-${d.type}`}>
                    <span className="decision-type">{d.type}</span>
                    {d.choice && <span className="decision-choice">{d.choice}</span>}
                    {d.status && <span className={`trace-badge ${d.status}`}>{d.status}</span>}
                    {d.row_count != null && <span className="trace-meta">{d.row_count} rows</span>}
                    {d.why && <div className="decision-why">{d.why}</div>}
                    {d.sql && <pre className="trace-sql">{d.sql}</pre>}
                  </li>
                ))}
              </ol>
            )}

            {/* Legacy hand-built steps */}
            {s.sql && <pre className="trace-sql">{s.sql}</pre>}
            {s.error && <div className="trace-err">{s.error}</div>}
            {s.fact && <div className="trace-fact">“{s.fact}”</div>}
          </li>
        ))}
      </ol>
    </div>
  );
}
