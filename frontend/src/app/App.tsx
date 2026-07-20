// App shell: auth gate, header, routed tab nav. Conversation state lives here
// (not in ChatPage) so switching tabs never loses the thread; sqlSeed carries
// "Open in SQL editor" from a chat report into the editor.
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  AskProgress,
  AskResult,
  askStream,
  ConversationMessage,
  getConversationMessages,
  PageFrame,
  PagePlanSlot,
  track,
  User,
} from "../lib/api";
import { loadAuthConfig, loginDev, logout as authLogout, resumeSession } from "../lib/auth";
import { getTheme, setTheme } from "../lib/theme";
import { MOBILE_QUERY, useMediaQuery } from "../lib/useMediaQuery";
import { ChatMsg, ChatPage, ConversationList } from "../features/chat/ChatPage";
import { AdminPage } from "../features/admin/AdminPage";
import { EvalsPage } from "../features/evals/EvalsPage";
import { GoldensPage } from "../features/goldens/GoldensPage";
import { SettingsPage } from "../features/settings/SettingsPage";
import { NavRail, View } from "./NavRail";
import { BottomNav, MobileTopBar } from "./MobileNav";
import { Sheet } from "../ui/Sheet";
import { IconHistory } from "../ui/icons";

// Code-split the SQL editor: CodeMirror only loads when the tab is opened.
const SqlEditor = lazy(() =>
  import("../features/sql/SqlEditor").then((m) => ({ default: m.SqlEditor })),
);
// Explore code-splits too — visx + the map layer only load on the Explore tab.
const ExplorePage = lazy(() =>
  import("../features/explore/ExplorePage").then((m) => ({ default: m.ExplorePage })),
);
import { Command, CommandPalette } from "../ui/CommandPalette";
import { ChartSqlContext } from "../ui/charts/sqlLink";
import { Login } from "./Login";

/** Rebuild a renderable result from a stored assistant message (history reopen).
 *  Result meta (engine/tokens/latency) and the admin agent trace are joined
 *  from the message's query_run by the API, so a reopened answer shows the same
 *  trace expander an in-session answer does — not just report-bearing ones. */
function messageToChat(m: ConversationMessage): ChatMsg {
  if (m.role === "user") return { role: "user", content: m.content };
  const report = m.report;
  const hasRenderable = report != null || m.steps.length > 0 || m.sql_generated != null;
  const result: AskResult | undefined = hasRenderable
    ? {
        conversation_id: "",
        message_id: m.id,
        run_id: m.run_id ?? "",
        answer: m.content,
        sql: m.sql_generated,
        columns: [],
        rows: [],
        row_count: 0,
        chart: null,
        engine: m.engine ?? "history",
        input_tokens: m.input_tokens,
        output_tokens: m.output_tokens,
        latency_ms: m.latency_ms,
        steps: m.steps,
        report,
        pages: report?.pages ?? null,
      }
    : undefined;
  return { role: "assistant", content: m.content, result };
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  // True until the initial resumeSession() call resolves, so a valid session
  // (dev-mode's httpOnly cookie) is not masked by a flash of the login screen
  // while we find out it exists.
  const [resuming, setResuming] = useState(true);
  const [authMode, setAuthMode] = useState<"dev" | "google">("dev");
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sqlSeed, setSqlSeed] = useState<{ sql: string; nonce: number } | null>(null);
  // Deep-link a promoted golden into the Goldens tab (mirrors sqlSeed): the
  // nonce forces the effect to re-fire even when the same id is promoted twice.
  const [goldenSeed, setGoldenSeed] = useState<{ id: string; nonce: number } | null>(null);
  const [working, setWorking] = useState<string | null>(null);
  const [progress, setProgress] = useState<AskProgress[]>([]);
  // s10 streaming pages: the answer's page plan (ghost slots + locked teasers)
  // and each page frame as it streams, keyed by page index.
  const [pagePlan, setPagePlan] = useState<PagePlanSlot[]>([]);
  const [streamedPages, setStreamedPages] = useState<Record<number, PageFrame>>({});
  const [paletteOpen, setPaletteOpen] = useState(false);
  // Mobile tier renders BottomNav + sheets instead of the icon rail.
  const isMobile = useMediaQuery(MOBILE_QUERY);
  const [convSheetOpen, setConvSheetOpen] = useState(false);
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
  const view: View = location.pathname.startsWith("/explore")
    ? "explore"
    : location.pathname.startsWith("/sql")
      ? "sql"
      : location.pathname.startsWith("/goldens")
        ? "goldens"
        : location.pathname.startsWith("/evals")
          ? "evals"
          : location.pathname.startsWith("/admin")
          ? "admin"
          : location.pathname.startsWith("/settings")
            ? "settings"
            : "chat";
  const setView = useCallback((v: View) => navigate(`/${v}`), [navigate]);

  // Normalize unknown paths and guard the admin route by role.
  useEffect(() => {
    const known = ["/chat", "/explore", "/sql", "/goldens", "/evals", "/admin", "/settings"];
    const adminOnly = ["/goldens", "/evals", "/admin"];
    if (!known.some((p) => location.pathname.startsWith(p))) {
      navigate("/chat", { replace: true });
    } else if (
      user &&
      adminOnly.some((p) => location.pathname.startsWith(p)) &&
      user.role !== "admin"
    ) {
      navigate("/chat", { replace: true });
    }
  }, [location.pathname, user, navigate]);

  function openInSqlEditor(sqlText: string) {
    setSqlSeed({ sql: sqlText, nonce: Date.now() });
    setView("sql");
  }

  // Land on the Goldens tab with a just-promoted golden loaded in the editor.
  function openInGoldens(goldenId: string) {
    setGoldenSeed({ id: goldenId, nonce: Date.now() });
    setView("goldens");
  }

  useEffect(() => {
    track("login_screen_view");
    // Discover which auth backend is active (dev stub or Google Sign-in).
    loadAuthConfig()
      .then((cfg) => setAuthMode(cfg.auth_mode))
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A page reload wipes the in-memory bearer token, but dev-mode also sets an
  // httpOnly session cookie that survives it. Without this, the cookie fix in
  // lib/auth.ts is invisible to the user: the backend would accept the
  // session, but nothing ever asked it to, so the login screen showed anyway.
  useEffect(() => {
    let cancelled = false;
    resumeSession()
      .then((u) => {
        if (cancelled) return;
        if (u) enterApp(u);
      })
      .finally(() => {
        if (!cancelled) setResuming(false);
      });
    return () => {
      cancelled = true;
    };
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

  // Google Sign-in: the GIS button (rendered in Login) calls back with our
  // provisioned profile once the ID token is verified by the backend.
  function handleGoogleUser(u: User) {
    setError(null);
    track("login_success", { username: u.username });
    enterApp(u);
  }

  function handleGoogleError(message: string) {
    track("login_failure", { reason: message });
    setError(message);
  }

  async function logout() {
    await authLogout();
    setUser(null);
    setView("chat");
    setMessages([]);
    setConversationId(null);
    queryClient.clear();
  }

  // Stop button aborts the in-flight SSE fetch; the run may finish server-side
  // but the UI returns to the composer immediately.
  const abortRef = useRef<AbortController | null>(null);

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
    setProgress([]);
    setPagePlan([]);
    setStreamedPages({});
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const result = await askStream(
        q,
        conversationId,
        (s) => {
          if (s.state === "working" && s.elapsed_s != null) {
            setWorking(`Agent is working… ${s.elapsed_s}s`);
          }
        },
        (p) => setProgress((prev) => [...prev, p]),
        // A re-plan (salvage/stub path) replaces the slots wholesale.
        (slots) => {
          setPagePlan(slots);
        },
        (frame) => setStreamedPages((prev) => ({ ...prev, [frame.index]: frame })),
        ctrl.signal,
      );
      setConversationId(result.conversation_id);
      setMessages((m) => [...m, { role: "assistant", content: result.answer, result }]);
      if (isNewConversation) {
        void queryClient.invalidateQueries({ queryKey: ["conversations"] });
        // The agent summarises the sidebar title in the background just after the
        // stream closes (s17 E1); refetch once more to swap in the real title.
        window.setTimeout(() => {
          void queryClient.invalidateQueries({ queryKey: ["conversations"] });
        }, 2500);
      }
    } catch (e) {
      if (ctrl.signal.aborted) {
        track("question_stopped", { question: q });
        setMessages((m) => [
          ...m,
          { role: "assistant", content: "Stopped — ask again whenever you're ready." },
        ]);
      } else {
        setError((e as Error).message);
        setMessages((m) => [
          ...m,
          { role: "assistant", content: "Sorry — something went wrong answering that." },
        ]);
      }
    } finally {
      abortRef.current = null;
      setLoading(false);
      setWorking(null);
      setPagePlan([]);
      setStreamedPages({});
    }
  }

  function stopStreaming() {
    abortRef.current?.abort();
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
    { id: "explore", label: "Go to Explore", hint: "navigate", run: () => setView("explore") },
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

  // Nothing to render yet either way: showing Login here would flash it for
  // the common case where the session resumes successfully a moment later.
  if (resuming) {
    return null;
  }

  if (!user) {
    return (
      <Login
        authMode={authMode}
        error={error}
        onDevLogin={handleDevLogin}
        onUser={handleGoogleUser}
        onError={handleGoogleError}
      />
    );
  }

  return (
    <ChartSqlContext.Provider value={openInSqlEditor}>
    <div className="app">
      <CommandPalette open={paletteOpen} commands={commands} onClose={() => setPaletteOpen(false)} />
      {!isMobile && (
        <NavRail view={view} setView={setView} user={user} onSignOut={() => void logout()} />
      )}
      <div className="app-body">
        {isMobile && (
          <MobileTopBar
            user={user}
            onSignOut={() => void logout()}
            action={
              view === "chat" ? (
                <button
                  className="rail-item"
                  aria-label="Conversation history"
                  title="Conversation history"
                  onClick={() => setConvSheetOpen(true)}
                >
                  <IconHistory />
                </button>
              ) : undefined
            }
          />
        )}
        <div className="view-host" key={view}>
          {view === "admin" && <AdminPage />}
          {view === "goldens" && <GoldensPage seed={goldenSeed} />}
          {view === "evals" && <EvalsPage />}
          {view === "settings" && <SettingsPage user={user} />}
          {view === "explore" && (
            <Suspense
              fallback={
                <main aria-busy="true">
                  <div className="skel" style={{ height: 40, marginBottom: 10 }} />
                  <div className="skel" style={{ height: 280 }} />
                </main>
              }
            >
              <ExplorePage isAdmin={user.role === "admin"} />
            </Suspense>
          )}
          {view === "sql" && (
            <Suspense
              fallback={
                <main aria-busy="true">
                  <div className="skel" style={{ height: 40, marginBottom: 10 }} />
                  <div className="skel" style={{ height: 240, marginBottom: 10 }} />
                  <div className="skel" style={{ height: 18, width: "40%" }} />
                </main>
              }
            >
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
              progress={progress}
              pagePlan={pagePlan}
              streamedPages={streamedPages}
              error={error}
              input={input}
              setInput={setInput}
              onSend={send}
              onStop={stopStreaming}
              onOpenSql={openInSqlEditor}
              onPromoteToGolden={openInGoldens}
              conversationId={conversationId}
              onOpenConversation={openConversation}
              onNewConversation={newConversation}
            />
          )}
        </div>
        {isMobile && (
          <BottomNav view={view} setView={setView} isAdmin={user.role === "admin"} />
        )}
      </div>
      {isMobile && (
        <Sheet open={convSheetOpen} onClose={() => setConvSheetOpen(false)} label="Conversations">
          <ConversationList
            activeId={conversationId}
            onOpen={(id) => {
              void openConversation(id);
              setConvSheetOpen(false);
            }}
            onNew={() => {
              newConversation();
              setConvSheetOpen(false);
            }}
          />
        </Sheet>
      )}
    </div>
    </ChartSqlContext.Provider>
  );
}
