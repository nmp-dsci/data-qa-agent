// Admin dashboard: metrics band, config, datasets/users/query-runs tables,
// feedback triage + eval cases, event stream. Data comes through react-query
// so mutations refresh by invalidation instead of hand-rolled reload loops.
import { Fragment, useCallback, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getAdminConfig,
  getAdminDatasets,
  getAdminEvents,
  getAdminFeedback,
  getAdminQueryRuns,
  getAdminUsers,
  getEvalCases,
} from "../../lib/api";
import { formatTime } from "../../lib/format";
import { AgentTrace, RunId, traceSummary } from "../../ui/AgentTrace";
import { AgentConfigView } from "./AgentConfigView";
import { ConfigView } from "./ConfigView";
import { FeedbackAdmin } from "./FeedbackAdmin";

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

type AdminTab = "observability" | "quality" | "agent-config";

const ADMIN_TABS: { id: AdminTab; label: string }[] = [
  { id: "observability", label: "Observability" },
  { id: "quality", label: "Quality" },
  { id: "agent-config", label: "Agent-Config" },
];

export function AdminPage() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<AdminTab>("observability");
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [eventUserFilter, setEventUserFilter] = useState("");
  const [expandedRun, setExpandedRun] = useState<string | null>(null);

  const eventsQ = useQuery({ queryKey: ["admin", "events"], queryFn: getAdminEvents });
  const usersQ = useQuery({ queryKey: ["admin", "users"], queryFn: getAdminUsers });
  const datasetsQ = useQuery({ queryKey: ["admin", "datasets"], queryFn: getAdminDatasets });
  const queryRunsQ = useQuery({ queryKey: ["admin", "query-runs"], queryFn: getAdminQueryRuns });
  const feedbackQ = useQuery({ queryKey: ["admin", "feedback"], queryFn: getAdminFeedback });
  const evalCasesQ = useQuery({ queryKey: ["admin", "eval-cases"], queryFn: getEvalCases });
  const configQ = useQuery({ queryKey: ["admin", "config"], queryFn: getAdminConfig });

  const refreshLoop = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["admin", "feedback"] }),
      queryClient.invalidateQueries({ queryKey: ["admin", "eval-cases"] }),
    ]);
  }, [queryClient]);

  const queries = [eventsQ, usersQ, datasetsQ, queryRunsQ, feedbackQ, evalCasesQ, configQ];
  const loading = queries.some((q) => q.isLoading);
  const error = queries.find((q) => q.error)?.error as Error | undefined;

  const events = eventsQ.data ?? [];
  const users = usersQ.data ?? [];
  const datasets = datasetsQ.data ?? [];
  const queryRuns = queryRunsQ.data ?? [];
  const feedback = feedbackQ.data ?? [];
  const evalCases = evalCasesQ.data ?? [];
  const config = configQ.data ?? null;

  return (
    <main className="admin">
      <section className="admin-band">
        <h2>Admin Dashboard</h2>
        <div className="admin-subtabs">
          {ADMIN_TABS.map((t) => (
            <button
              key={t.id}
              className={tab === t.id ? "chip active" : "chip"}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        {tab === "observability" && (
          <div className="metrics">
            <Metric label="Users" value={users.length} />
            <Metric label="Datasets" value={datasets.length} />
            <Metric label="Events" value={events.length} />
            <Metric label="Query runs" value={queryRuns.length} />
          </div>
        )}
      </section>
      {tab === "agent-config" && <AgentConfigView />}
      {tab === "quality" &&
        (loading ? (
          <p className="muted">Loading admin data...</p>
        ) : (
          <FeedbackAdmin feedback={feedback} evalCases={evalCases} onRefresh={refreshLoop} />
        ))}
      {tab === "observability" && loading && <p className="muted">Loading admin data...</p>}
      {tab === "observability" && error && <p className="error">{error.message}</p>}
      {tab === "observability" && !loading && (
        <>
          {config && <ConfigView config={config} />}
          <section>
            <h3>Datasets</h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Slug</th>
                    <th>Name</th>
                    <th>Status</th>
                    <th>Rows</th>
                    <th>Access</th>
                  </tr>
                </thead>
                <tbody>
                  {datasets.map((d) => (
                    <tr key={d.id}>
                      <td>{d.slug}</td>
                      <td>{d.name}</td>
                      <td>{d.status}</td>
                      <td>{d.row_count}</td>
                      <td>{d.access_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section>
            <h3>Users</h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>User</th>
                    <th>Email</th>
                    <th>Role</th>
                    <th>Last active</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.id}>
                      <td>{u.display_name}</td>
                      <td>{u.email}</td>
                      <td>{u.role}</td>
                      <td>{u.last_active ? formatTime(u.last_active) : "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section>
            <h3>Query Runs</h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Run ID</th>
                    <th>Time</th>
                    <th>User</th>
                    <th>Source</th>
                    <th>Channel</th>
                    <th>Dataset</th>
                    <th>Rows</th>
                    <th>Latency</th>
                    <th>Tokens (in/out)</th>
                    <th>Question / SQL</th>
                    <th>Agent run</th>
                  </tr>
                </thead>
                <tbody>
                  {queryRuns.map((q) => {
                    const stepCount = q.trace?.length ?? 0;
                    const open = expandedRun === q.id;
                    return (
                      <Fragment key={q.id}>
                        <tr>
                          <td>
                            <RunId id={q.id} />
                          </td>
                          <td>{formatTime(q.created_at)}</td>
                          <td>{q.username}</td>
                          <td>
                            <span className={`badge src-${q.source}`}>{q.source}</span>
                          </td>
                          <td>
                            <span className={`badge chan-${q.channel}`}>{q.channel}</span>
                          </td>
                          <td>{q.dataset ?? "-"}</td>
                          <td>{q.row_count}</td>
                          <td>{q.latency_ms ?? "-"} ms</td>
                          <td>
                            {q.input_tokens != null && q.output_tokens != null
                              ? `${q.input_tokens}/${q.output_tokens}`
                              : "-"}
                          </td>
                          <td className="wide-cell">{q.question ?? q.sql_text ?? "-"}</td>
                          <td>
                            {stepCount > 0 ? (
                              <button
                                className="link"
                                onClick={() => setExpandedRun(open ? null : q.id)}
                              >
                                {open ? "hide" : `${stepCount} step${stepCount === 1 ? "" : "s"}`}
                              </button>
                            ) : (
                              "-"
                            )}
                          </td>
                        </tr>
                        {open && q.trace && (
                          <tr className="trace-row">
                            <td colSpan={11}>
                              <div className="run-inspect-hint">
                                run <code>{q.id}</code> — inspect end-to-end:{" "}
                                <code>uv run python scripts/inspect_run.py {q.id}</code>
                              </div>
                              <AgentTrace
                                steps={q.trace}
                                summary={traceSummary({
                                  engine: q.engine,
                                  steps: q.trace,
                                  latency_ms: q.latency_ms,
                                  input_tokens: q.input_tokens,
                                  output_tokens: q.output_tokens,
                                })}
                              />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
          <section>
            <h3>Events</h3>
            <div className="event-filters">
              <select value={eventTypeFilter} onChange={(e) => setEventTypeFilter(e.target.value)}>
                <option value="">All event types</option>
                {[...new Set(events.map((e) => e.event_type))].sort().map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
              <select value={eventUserFilter} onChange={(e) => setEventUserFilter(e.target.value)}>
                <option value="">All users</option>
                {[...new Set(events.map((e) => e.username ?? "anonymous"))].sort().map((u) => (
                  <option key={u} value={u}>
                    {u}
                  </option>
                ))}
              </select>
            </div>
            <div className="event-list">
              {events
                .filter((e) => !eventTypeFilter || e.event_type === eventTypeFilter)
                .filter((e) => !eventUserFilter || (e.username ?? "anonymous") === eventUserFilter)
                .map((e) => (
                  <div key={e.id} className="event-row">
                    <span>{formatTime(e.created_at)}</span>
                    <strong>{e.event_type}</strong>
                    <span>{e.username ?? "anonymous"}</span>
                  </div>
                ))}
            </div>
          </section>
        </>
      )}
    </main>
  );
}
