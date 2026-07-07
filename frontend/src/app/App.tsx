// App shell: auth gate, header, routed tab nav. Conversation state lives here
// (not in ChatPage) so switching tabs never loses the thread; sqlSeed carries
// "Open in SQL editor" from a chat report into the editor.
import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ask, track, User } from "../lib/api";
import { bootstrap, loadAuthConfig, loginDev, loginEntra, logout as authLogout } from "../lib/auth";
import { ChatMsg, ChatPage } from "../features/chat/ChatPage";
import { AdminPage } from "../features/admin/AdminPage";
import { SqlEditor } from "../features/sql/SqlEditor";
import { Login } from "./Login";

type View = "chat" | "sql" | "admin";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [authMode, setAuthMode] = useState<"dev" | "entra">("dev");
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sqlSeed, setSqlSeed] = useState<{ sql: string; nonce: number } | null>(null);

  // Tabs are real routes: /chat, /sql, /admin (deep-linkable, back-button aware).
  const location = useLocation();
  const navigate = useNavigate();
  const view: View = location.pathname.startsWith("/sql")
    ? "sql"
    : location.pathname.startsWith("/admin")
      ? "admin"
      : "chat";
  const setView = useCallback((v: View) => navigate(`/${v}`), [navigate]);

  // Normalize unknown paths and guard the admin route by role.
  useEffect(() => {
    const known = ["/chat", "/sql", "/admin"];
    if (!known.some((p) => location.pathname.startsWith(p))) {
      navigate("/chat", { replace: true });
    } else if (user && location.pathname.startsWith("/admin") && user.role !== "admin") {
      navigate("/chat", { replace: true });
    }
  }, [location.pathname, user, navigate]);

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      <Login
        authMode={authMode}
        error={error}
        onDevLogin={handleDevLogin}
        onEntraLogin={handleEntraLogin}
      />
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

      {view === "admin" && <AdminPage />}
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
        <ChatPage
          user={user}
          messages={messages}
          loading={loading}
          error={error}
          input={input}
          setInput={setInput}
          onSend={send}
          onOpenSql={openInSqlEditor}
        />
      )}
    </div>
  );
}
