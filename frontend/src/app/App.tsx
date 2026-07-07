// App shell: auth gate, header, routed tab nav. Conversation state lives here
// (not in ChatPage) so switching tabs never loses the thread; sqlSeed carries
// "Open in SQL editor" from a chat report into the editor.
import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  AskResult,
  askStream,
  ConversationMessage,
  getConversationMessages,
  track,
  User,
} from "../lib/api";
import { bootstrap, loadAuthConfig, loginDev, loginEntra, logout as authLogout } from "../lib/auth";
import { getTheme, setTheme } from "../lib/theme";
import { ChatMsg, ChatPage } from "../features/chat/ChatPage";
import { AdminPage } from "../features/admin/AdminPage";
import { SettingsPage } from "../features/settings/SettingsPage";

// Code-split the SQL editor: CodeMirror only loads when the tab is opened.
const SqlEditor = lazy(() =>
  import("../features/sql/SqlEditor").then((m) => ({ default: m.SqlEditor })),
);
import { Command, CommandPalette } from "../ui/CommandPalette";
import { Login } from "./Login";

type View = "chat" | "sql" | "admin" | "settings";

/** Rebuild a renderable result from a stored assistant message (history reopen). */
function messageToChat(m: ConversationMessage): ChatMsg {
  if (m.role === "user") return { role: "user", content: m.content };
  const report = m.report;
  const result: AskResult | undefined = report
    ? {
        conversation_id: "",
        message_id: m.id,
        run_id: "",
        answer: m.content,
        sql: m.sql_generated,
        columns: [],
        rows: [],
        row_count: 0,
        chart: null,
        engine: "history",
        input_tokens: null,
        output_tokens: null,
        latency_ms: null,
        steps: [],
        report,
        pages: report.pages ?? null,
      }
    : undefined;
  return { role: "assistant", content: m.content, result };
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [authMode, setAuthMode] = useState<"dev" | "entra">("dev");
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sqlSeed, setSqlSeed] = useState<{ sql: string; nonce: number } | null>(null);
  const [working, setWorking] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const queryClient = useQueryClient();

  // ⌘K / Ctrl+K opens the command palette anywhere in the app.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Tabs are real routes: /chat, /sql, /admin, /settings (deep-linkable).
  const location = useLocation();
  const navigate = useNavigate();
  const view: View = location.pathname.startsWith("/sql")
    ? "sql"
    : location.pathname.startsWith("/admin")
      ? "admin"
      : location.pathname.startsWith("/settings")
        ? "settings"
        : "chat";
  const setView = useCallback((v: View) => navigate(`/${v}`), [navigate]);

  // Normalize unknown paths and guard the admin route by role.
  useEffect(() => {
    const known = ["/chat", "/sql", "/admin", "/settings"];
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
    queryClient.clear();
  }

  async function send(question: string) {
    const q = question.trim();
    if (!q || loading) return;
    setInput("");
    setError(null);
    setMessages((m) => [...m, { role: "user", content: q }]);
    setLoading(true);
    track("question_submitted", { question: q });
    const isNewConversation = conversationId === null;
    setWorking("Agent is working…");
    try {
      const result = await askStream(q, conversationId, (s) => {
        if (s.state === "working" && s.elapsed_s != null) {
          setWorking(`Agent is working… ${s.elapsed_s}s`);
        }
      });
      setConversationId(result.conversation_id);
      setMessages((m) => [...m, { role: "assistant", content: result.answer, result }]);
      if (isNewConversation) {
        void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      }
    } catch (e) {
      setError((e as Error).message);
      setMessages((m) => [
        ...m,
        { role: "assistant", content: "Sorry — something went wrong answering that." },
      ]);
    } finally {
      setLoading(false);
      setWorking(null);
    }
  }

  async function openConversation(id: string) {
    if (id === conversationId) return;
    setError(null);
    try {
      const msgs = await getConversationMessages(id);
      setMessages(msgs.map(messageToChat));
      setConversationId(id);
      track("conversation_reopened", { conversation_id: id });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function newConversation() {
    setMessages([]);
    setConversationId(null);
    setError(null);
    setInput("");
    track("conversation_new");
  }

  const commands: Command[] = [
    { id: "chat", label: "Go to Chat", hint: "navigate", run: () => setView("chat") },
    { id: "sql", label: "Go to SQL Editor", hint: "navigate", run: () => setView("sql") },
    ...(user?.role === "admin"
      ? [{ id: "admin", label: "Go to Admin", hint: "navigate", run: () => setView("admin") }]
      : []),
    { id: "settings", label: "Go to Settings", hint: "navigate", run: () => setView("settings") },
    {
      id: "new-conv",
      label: "New conversation",
      hint: "chat",
      run: () => {
        newConversation();
        setView("chat");
      },
    },
    {
      id: "theme",
      label: `Switch to ${getTheme() === "dark" ? "light" : "dark"} theme`,
      hint: "appearance",
      run: () => setTheme(getTheme() === "dark" ? "light" : "dark"),
    },
    { id: "signout", label: "Sign out", hint: "session", run: () => void logout() },
  ];

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
      <CommandPalette open={paletteOpen} commands={commands} onClose={() => setPaletteOpen(false)} />
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

      <nav className="tabs" role="tablist" aria-label="App sections">
        <button
          role="tab"
          aria-selected={view === "chat"}
          className={view === "chat" ? "tab active" : "tab"}
          onClick={() => setView("chat")}
        >
          Chat
        </button>
        <button
          role="tab"
          aria-selected={view === "sql"}
          className={view === "sql" ? "tab active" : "tab"}
          onClick={() => setView("sql")}
        >
          SQL Editor
        </button>
        {user.role === "admin" && (
          <button
            role="tab"
            aria-selected={view === "admin"}
            className={view === "admin" ? "tab active" : "tab"}
            onClick={() => setView("admin")}
          >
            Admin
          </button>
        )}
        <button
          role="tab"
          aria-selected={view === "settings"}
          className={view === "settings" ? "tab active" : "tab"}
          onClick={() => setView("settings")}
        >
          Settings
        </button>
      </nav>

      {view === "admin" && <AdminPage />}
      {view === "settings" && <SettingsPage user={user} />}
      {view === "sql" && (
        <Suspense fallback={<main className="muted">Loading SQL editor…</main>}>
          <SqlEditor
            user={user}
            seedSql={sqlSeed}
            onSendToChat={(text) => {
              setView("chat");
              setInput(text);
            }}
          />
        </Suspense>
      )}
      {view === "chat" && (
        <ChatPage
          user={user}
          messages={messages}
          loading={loading}
          working={working}
          error={error}
          input={input}
          setInput={setInput}
          onSend={send}
          onOpenSql={openInSqlEditor}
          conversationId={conversationId}
          onOpenConversation={openConversation}
          onNewConversation={newConversation}
        />
      )}
    </div>
  );
}
