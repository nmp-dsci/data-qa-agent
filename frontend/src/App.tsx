import { useEffect, useState } from "react";
import {
  AdminDataset,
  AdminEvent,
  AdminQueryRun,
  AdminUser,
  ask,
  AskResult,
  devLogin,
  getAdminDatasets,
  getAdminEvents,
  getAdminQueryRuns,
  getAdminUsers,
  setToken,
  track,
  User,
} from "./api";

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
  result?: AskResult;
}

const TEST_USERS = [
  { username: "admin", label: "Admin", hint: "sees all data" },
  { username: "user1", label: "User One", hint: "has housing access" },
  { username: "user2", label: "User Two", hint: "no housing access (isolated)" },
];

const SUGGESTIONS = [
  "What is the average sale price by suburb?",
  "How many properties are there?",
  "What are the 5 most expensive properties?",
  "Average price by property type?",
];

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [view, setView] = useState<"chat" | "admin">("chat");
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    track("login_screen_view");
  }, []);

  async function handleLogin(username: string) {
    setError(null);
    try {
      const { access_token, user } = await devLogin(username);
      setToken(access_token);
      setUser(user);
      setView("chat");
      setMessages([]);
      setConversationId(null);
      track("home_view", { username });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function logout() {
    setToken(null);
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
          <p className="sub">Ask questions about your data. Sign in as a test user:</p>
          <div className="users">
            {TEST_USERS.map((u) => (
              <button key={u.username} onClick={() => handleLogin(u.username)}>
                <strong>{u.label}</strong>
                <span>{u.hint}</span>
              </button>
            ))}
          </div>
          {error && <p className="error">{error}</p>}
          <p className="foot">Dev-auth stub · production uses Microsoft Entra External ID</p>
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
          {user.role === "admin" && (
            <button className="ghost" onClick={() => setView(view === "chat" ? "admin" : "chat")}>
              {view === "chat" ? "Admin" : "Chat"}
            </button>
          )}
          <button className="ghost" onClick={logout}>
            Sign out
          </button>
        </div>
      </header>

      {view === "admin" ? (
        <AdminDashboard />
      ) : (
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
                {m.result && <ResultView result={m.result} />}
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
            placeholder="Ask a question about the housing data…"
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([getAdminEvents(), getAdminUsers(), getAdminDatasets(), getAdminQueryRuns()])
      .then(([events, users, datasets, queryRuns]) => {
        if (!active) return;
        setEvents(events);
        setUsers(users);
        setDatasets(datasets);
        setQueryRuns(queryRuns);
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
                  </tr>
                </thead>
                <tbody>
                  {datasets.map((d) => (
                    <tr key={d.id}>
                      <td>{d.slug}</td>
                      <td>{d.name}</td>
                      <td>{d.status}</td>
                      <td>{d.row_count}</td>
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
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.id}>
                      <td>{u.display_name}</td>
                      <td>{u.email}</td>
                      <td>{u.role}</td>
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
                    <th>Time</th>
                    <th>User</th>
                    <th>Dataset</th>
                    <th>Rows</th>
                    <th>Latency</th>
                    <th>Question</th>
                  </tr>
                </thead>
                <tbody>
                  {queryRuns.map((q) => (
                    <tr key={q.id}>
                      <td>{formatTime(q.created_at)}</td>
                      <td>{q.username}</td>
                      <td>{q.dataset ?? "-"}</td>
                      <td>{q.row_count}</td>
                      <td>{q.latency_ms ?? "-"} ms</td>
                      <td className="wide-cell">{q.question}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section>
            <h3>Events</h3>
            <div className="event-list">
              {events.map((e) => (
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

function ResultView({ result }: { result: AskResult }) {
  const [showSql, setShowSql] = useState(false);
  if (result.row_count === 0 && !result.sql) return null;
  return (
    <div className="result">
      <div className="meta">
        <span className={`badge ${result.engine}`}>{result.engine}</span>
        <span>{result.row_count} rows</span>
        {result.sql && (
          <button className="link" onClick={() => setShowSql((s) => !s)}>
            {showSql ? "hide SQL" : "show SQL"}
          </button>
        )}
      </div>
      {showSql && result.sql && <pre className="sql">{result.sql}</pre>}
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
    </div>
  );
}
