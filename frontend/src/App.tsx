import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import {
  AdminDataset,
  AdminEvent,
  AdminFeedback,
  AdminQueryRun,
  AdminUser,
  AgentStep,
  ask,
  AskResult,
  EvalCase,
  getAdminDatasets,
  getAdminEvents,
  getAdminFeedback,
  getAdminQueryRuns,
  getAdminUsers,
  getEvalCases,
  Headline,
  InsightReport,
  promoteFeedback,
  QueryRef,
  runEvalStaleness,
  setEvalCaseStatus,
  submitFeedback,
  track,
  triageFeedback,
  User,
} from "./api";
import { bootstrap, loadAuthConfig, loginDev, loginEntra, logout as authLogout } from "./auth";
import { VegaChart } from "./VegaChart";
import { SqlEditor } from "./SqlEditor";

type View = "chat" | "sql" | "admin";

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
  result?: AskResult;
}

const TEST_USERS = [
  { username: "admin", label: "Admin", hint: "sees all data" },
  { username: "user1", label: "User One", hint: "has property data access" },
  { username: "user2", label: "User Two", hint: "no data access (isolated)" },
];

const SUGGESTIONS = [
  "show me trend of sale price for houses for Normanhurst vs Hornsby for all time 2010 to 2026",
  "What are the top growth suburbs for sale price and rent?",
  "Which suburbs have the highest rent growth?",
  "Top suburbs by sale price growth?",
  "How many suburbs do we have?",
];

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [authMode, setAuthMode] = useState<"dev" | "entra">("dev");
  const [view, setView] = useState<View>("chat");
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sqlSeed, setSqlSeed] = useState<{ sql: string; nonce: number } | null>(null);

  function openInSqlEditor(sqlText: string) {
    setSqlSeed({ sql: sqlText, nonce: Date.now() });
    setView("sql");
  }

  useEffect(() => {
    track("login_screen_view");
    // Discover the auth backend and, for Entra, restore an existing session.
    loadAuthConfig()
      .then(async (cfg) => {
        setAuthMode(cfg.auth_mode);
        if (cfg.auth_mode === "entra") {
          const existing = await bootstrap();
          if (existing) enterApp(existing);
        }
      })
      .catch(() => {});
  }, []);

  function enterApp(u: User) {
    setUser(u);
    setView("chat");
    setMessages([]);
    setConversationId(null);
    track("home_view", { username: u.username });
  }

  async function handleDevLogin(username: string) {
    setError(null);
    try {
      const u = await loginDev(username);
      track("login_success", { username: u.username });
      enterApp(u);
    } catch (e) {
      track("login_failure", { username, reason: (e as Error).message });
      setError((e as Error).message);
    }
  }

  async function handleEntraLogin() {
    setError(null);
    try {
      const u = await loginEntra();
      track("login_success", { username: u.username });
      enterApp(u);
    } catch (e) {
      track("login_failure", { reason: (e as Error).message });
      setError((e as Error).message);
    }
  }

  async function logout() {
    await authLogout();
    setUser(null);
    setView("chat");
    setMessages([]);
    setConversationId(null);
  }

  async function send(question: string) {
    const q = question.trim();
    if (!q || loading) return;
    setInput("");
    setError(null);
    setMessages((m) => [...m, { role: "user", content: q }]);
    setLoading(true);
    track("question_submitted", { question: q });
    try {
      const result = await ask(q, conversationId);
      setConversationId(result.conversation_id);
      setMessages((m) => [...m, { role: "assistant", content: result.answer, result }]);
    } catch (e) {
      setError((e as Error).message);
      setMessages((m) => [
        ...m,
        { role: "assistant", content: "Sorry — something went wrong answering that." },
      ]);
    } finally {
      setLoading(false);
    }
  }

  if (!user) {
    return (
      <div className="login">
        <div className="login-card">
          <h1>data-qa-agent</h1>
          {authMode === "entra" ? (
            <>
              <p className="sub">Sign in to ask questions about your data.</p>
              <div className="users">
                <button onClick={handleEntraLogin}>
                  <strong>Sign in with Microsoft</strong>
                  <span>Entra External ID</span>
                </button>
              </div>
              {error && <p className="error">{error}</p>}
              <p className="foot">Secured by Microsoft Entra External ID</p>
            </>
          ) : (
            <>
              <p className="sub">Ask questions about your data. Sign in as a test user:</p>
              <div className="users">
                {TEST_USERS.map((u) => (
                  <button key={u.username} onClick={() => handleDevLogin(u.username)}>
                    <strong>{u.label}</strong>
                    <span>{u.hint}</span>
                  </button>
                ))}
              </div>
              {error && <p className="error">{error}</p>}
              <p className="foot">Dev-auth stub · production uses Microsoft Entra External ID</p>
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <header>
        <div>
          <strong>data-qa-agent</strong>
          <span className="pill">{user.display_name}</span>
          <span className={`pill role-${user.role}`}>{user.role}</span>
        </div>
        <div className="header-actions">
          <button className="ghost" onClick={logout}>
            Sign out
          </button>
        </div>
      </header>

      <nav className="tabs">
        <button
          className={view === "chat" ? "tab active" : "tab"}
          onClick={() => setView("chat")}
        >
          Chat
        </button>
        <button className={view === "sql" ? "tab active" : "tab"} onClick={() => setView("sql")}>
          SQL Editor
        </button>
        {user.role === "admin" && (
          <button
            className={view === "admin" ? "tab active" : "tab"}
            onClick={() => setView("admin")}
          >
            Admin
          </button>
        )}
      </nav>

      {view === "admin" && <AdminDashboard />}
      {view === "sql" && (
        <SqlEditor
          user={user}
          seedSql={sqlSeed}
          onSendToChat={(sqlText) => {
            setView("chat");
            setInput(sqlText);
          }}
        />
      )}
      {view === "chat" && (
        <main>
          {messages.length === 0 && (
            <div className="empty">
              <p>Try asking:</p>
              <div className="suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s} onClick={() => send(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              <div className="bubble">
                <div className="who">{m.role === "user" ? user.display_name : "Data agent"}</div>
                <div className="content">{m.content}</div>
                {m.result && (
                  <ResultView
                    result={m.result}
                    isAdmin={user.role === "admin"}
                    onOpenSql={openInSqlEditor}
                  />
                )}
              </div>
            </div>
          ))}
          {loading && (
            <div className="msg assistant">
              <div className="bubble">Agent is working…</div>
            </div>
          )}
          {error && <p className="error">{error}</p>}
        </main>
      )}

      {view === "chat" && (
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
        >
          <input
            value={input}
            placeholder="Ask about NSW property growth by suburb…"
            onChange={(e) => setInput(e.target.value)}
          />
          <button type="submit" disabled={loading}>
            Ask
          </button>
        </form>
      )}
    </div>
  );
}

function AdminDashboard() {
  const [events, setEvents] = useState<AdminEvent[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [datasets, setDatasets] = useState<AdminDataset[]>([]);
  const [queryRuns, setQueryRuns] = useState<AdminQueryRun[]>([]);
  const [feedback, setFeedback] = useState<AdminFeedback[]>([]);
  const [evalCases, setEvalCases] = useState<EvalCase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [eventUserFilter, setEventUserFilter] = useState("");
  const [expandedRun, setExpandedRun] = useState<string | null>(null);

  const refreshLoop = useCallback(async () => {
    const [fb, ec] = await Promise.all([getAdminFeedback(), getEvalCases()]);
    setFeedback(fb);
    setEvalCases(ec);
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([
      getAdminEvents(),
      getAdminUsers(),
      getAdminDatasets(),
      getAdminQueryRuns(),
      getAdminFeedback(),
      getEvalCases(),
    ])
      .then(([events, users, datasets, queryRuns, fb, ec]) => {
        if (!active) return;
        setEvents(events);
        setUsers(users);
        setDatasets(datasets);
        setQueryRuns(queryRuns);
        setFeedback(fb);
        setEvalCases(ec);
        setError(null);
      })
      .catch((e) => {
        if (active) setError((e as Error).message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <main className="admin">
      <section className="admin-band">
        <h2>Admin Dashboard</h2>
        <div className="metrics">
          <Metric label="Users" value={users.length} />
          <Metric label="Datasets" value={datasets.length} />
          <Metric label="Events" value={events.length} />
          <Metric label="Query runs" value={queryRuns.length} />
        </div>
      </section>
      {loading && <p className="muted">Loading admin data...</p>}
      {error && <p className="error">{error}</p>}
      {!loading && (
        <>
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
          <FeedbackAdmin feedback={feedback} evalCases={evalCases} onRefresh={refreshLoop} />
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

function FeedbackAdmin({
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

function summarizeSnapshot(snap: Record<string, unknown>): string {
  if (!snap) return "";
  if (typeof snap.heading === "string") return snap.heading;
  if (typeof snap.label === "string") return `${snap.label}: ${snap.value ?? ""}`;
  const s = JSON.stringify(snap);
  return s.length > 120 ? s.slice(0, 120) + "…" : s;
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function formatTime(value: string) {
  return new Date(value).toLocaleString();
}

function RunId({ id }: { id: string }) {
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

function fmtTokens(n?: number | null): string {
  return n == null ? "—" : n.toLocaleString();
}

function traceSummary(opts: {
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

function AgentTrace({ steps, summary }: { steps: AgentStep[]; summary?: string }) {
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

function ResultView({
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
      {result.report ? (
        <ReportView report={result.report} messageId={result.message_id} onOpenSql={onOpenSql} />
      ) : (
        <LegacyResult result={result} />
      )}
    </div>
  );
}

interface Selected {
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

interface FeedbackMarker {
  rating: 1 | -1;
  accurate: boolean | null;
  issueFlag: boolean;
}

function ReportView({
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
            {feedbackMarkers[p.element_id] && <FeedbackMarkerIcon marker={feedbackMarkers[p.element_id]} />}
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
            {feedbackMarkers["report:chart"] && <FeedbackMarkerIcon marker={feedbackMarkers["report:chart"]} />}
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

function FeedbackMarkerIcon({ marker }: { marker: FeedbackMarker }) {
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

function ReportPreview({
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

function FeedbackBox({
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
      {result.chart && <VegaChart spec={result.chart} />}
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
