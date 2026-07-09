// Chat tab: conversations sidebar + message thread + composer. Conversation
// state lives in the app shell so it survives tab switches; the sidebar lists
// past conversations (new conversation = fresh thread, follow-ups in the same
// thread stay multi-turn).
import { useQuery } from "@tanstack/react-query";
import {
  AskProgress,
  AskResult,
  ConversationSummary,
  getConversations,
  PageFrame,
  PagePlanSlot,
  User,
} from "../../lib/api";
import { PageLayout } from "../../report-engine/PageLayout";
import { ResultView } from "./ResultView";

export interface ChatMsg {
  role: "user" | "assistant";
  content: string;
  result?: AskResult;
}

const SUGGESTIONS = [
  "show me trend of sale price for houses for Normanhurst vs Hornsby for all time 2010 to 2026",
  "What are the top growth suburbs for sale price and rent?",
  "Which suburbs have the highest rent growth?",
  "Top suburbs by sale price growth?",
  "How many suburbs do we have?",
];

function ConversationList({
  activeId,
  onOpen,
  onNew,
}: {
  activeId: string | null;
  onOpen: (id: string) => void;
  onNew: () => void;
}) {
  const q = useQuery({ queryKey: ["conversations"], queryFn: getConversations });
  const conversations: ConversationSummary[] = q.data ?? [];
  return (
    <aside className="conv-panel">
      <button className="conv-new" onClick={onNew}>
        + New conversation
      </button>
      <div className="schema-title">Conversations</div>
      {q.isLoading && <div className="muted sqled-hint">Loading…</div>}
      {conversations.map((c) => (
        <button
          key={c.id}
          className={`conv-item${c.id === activeId ? " active" : ""}`}
          onClick={() => onOpen(c.id)}
          title={c.title ?? ""}
        >
          <span className="conv-title">{c.title || "Untitled"}</span>
          <span className="conv-meta">
            {c.message_count} msg{c.message_count === 1 ? "" : "s"}
          </span>
        </button>
      ))}
      {!q.isLoading && conversations.length === 0 && (
        <div className="muted sqled-hint">No conversations yet.</div>
      )}
    </aside>
  );
}

const PAGE_KIND_LABELS: Record<string, string> = {
  summary: "Summary",
  insights: "Insights",
  opportunities: "Opportunities",
};

function pageKindLabel(kind: string): string {
  return PAGE_KIND_LABELS[kind] ?? kind;
}

/** Blacked-out object-shaped placeholders the agent will fill in — derived
 *  from the page kind (summary ⇒ kpi + note | chart; insights ⇒ notes | bars),
 *  mirroring the summary/insights template columns from the registry. */
function GhostPage({ kind }: { kind: string }) {
  const chart = (
    <div className="ghost-obj ghost-chart-box">
      <span className="ghost-cap">chart</span>
      <div className="ghost-chart" />
    </div>
  );
  const left =
    kind === "summary" ? (
      <>
        <div className="ghost-obj">
          <span className="ghost-cap">kpi</span>
          <div className="ghost-bar w40" />
          <div className="ghost-bar big" />
          <div className="ghost-bar w60" />
        </div>
        <div className="ghost-obj">
          <span className="ghost-cap">summary</span>
          <div className="ghost-bar w80" />
          <div className="ghost-bar w60" />
        </div>
      </>
    ) : (
      <>
        <div className="ghost-obj">
          <span className="ghost-cap">insight</span>
          <div className="ghost-bar w60" />
          <div className="ghost-bar w80" />
        </div>
        <div className="ghost-obj">
          <span className="ghost-cap">insight</span>
          <div className="ghost-bar w40" />
          <div className="ghost-bar w80" />
        </div>
      </>
    );
  return (
    <div className="ghost-grid">
      <div className="ghost-col">{left}</div>
      <div className="ghost-col">{chart}</div>
    </div>
  );
}

/** The streamed answer while the agent works: the running step list, then one
 *  section per planned page. Pages render through the SAME PageLayout the
 *  final answer uses the moment their frame lands; not-yet-started pages show
 *  ghost placeholders with progressive disclosure (the Page N+1 slot appears
 *  only after page N lands); locked plan entries render the paywall teaser. */
function WorkingBubble({
  working,
  progress,
  pagePlan,
  streamedPages,
}: {
  working?: string | null;
  progress: AskProgress[];
  pagePlan: PagePlanSlot[];
  streamedPages: Record<number, PageFrame>;
}) {
  const slots = [...pagePlan].sort((a, b) => a.index - b.index);
  const open = slots.filter((s) => s.status !== "locked");
  // Progressive disclosure: a slot is visible once every earlier open slot
  // has resolved (complete or skipped). The first slot shows immediately.
  const visibleUpTo = (index: number) =>
    open.filter((s) => s.index < index).every((s) => streamedPages[s.index] != null);
  return (
    <div className="msg assistant">
      <div className="bubble">
        <div className="working-head">{working ?? "Agent is working…"}</div>
        {progress.length > 0 && (
          <ol className="working-steps">
            {progress.map((p) => (
              <li key={p.n} className="working-step">
                <span className="working-step-n">{p.n}</span>
                <span className="working-step-action">{p.action}</span>
                {p.detail && <span className="working-step-detail">{p.detail}</span>}
              </li>
            ))}
          </ol>
        )}
        {slots.map((slot) => {
          const label = pageKindLabel(slot.kind);
          if (slot.status === "locked") {
            return (
              <div className="stream-page" key={slot.index}>
                <div className="stream-page-head">
                  Page {slot.index} · {label}
                  <span className="page-status locked">🔒 upgrade</span>
                </div>
                <div className="locked-teaser">
                  {label} pages are available on a higher plan.
                </div>
              </div>
            );
          }
          const frame = streamedPages[slot.index];
          if (frame?.status === "complete" && frame.page) {
            return (
              <div className="stream-page" key={slot.index}>
                <div className="stream-page-head">
                  Page {slot.index} · {label}
                  <span className="page-status done">✓ streamed</span>
                </div>
                <div className="report">
                  <PageLayout page={frame.page} />
                </div>
              </div>
            );
          }
          if (frame != null || !visibleUpTo(slot.index)) return null; // skipped / not yet disclosed
          return (
            <div className="stream-page" key={slot.index}>
              <div className="stream-page-head">
                Page {slot.index} · {label}
                <span className="page-status building">
                  {slot.index === 1 ? "populating…" : "working…"}
                </span>
              </div>
              <GhostPage kind={slot.kind} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function ChatPage({
  user,
  messages,
  loading,
  working,
  progress,
  pagePlan,
  streamedPages,
  error,
  input,
  setInput,
  onSend,
  onOpenSql,
  conversationId,
  onOpenConversation,
  onNewConversation,
}: {
  user: User;
  messages: ChatMsg[];
  loading: boolean;
  working?: string | null;
  progress: AskProgress[];
  pagePlan: PagePlanSlot[];
  streamedPages: Record<number, PageFrame>;
  error: string | null;
  input: string;
  setInput: (v: string) => void;
  onSend: (question: string) => void;
  onOpenSql: (sql: string) => void;
  conversationId: string | null;
  onOpenConversation: (id: string) => void;
  onNewConversation: () => void;
}) {
  return (
    <div className="chat-layout">
      <ConversationList
        activeId={conversationId}
        onOpen={onOpenConversation}
        onNew={onNewConversation}
      />
      <div className="chat-main">
        <main>
          {messages.length === 0 && (
            <div className="empty">
              <p>Try asking:</p>
              <div className="suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s} onClick={() => onSend(s)}>
                    {s}
                  </button>
                ))}
              </div>
              <p className="onboard-hint">
                Answers open with a <b>Summary</b> (latest number + growth) and an{" "}
                <b>Insights</b> page explaining it. Click any element to leave feedback ·{" "}
                <kbd>⌘K</kbd> opens the command palette.
              </p>
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
                    onOpenSql={onOpenSql}
                  />
                )}
              </div>
            </div>
          ))}
          {loading && (
            <WorkingBubble
              working={working}
              progress={progress}
              pagePlan={pagePlan}
              streamedPages={streamedPages}
            />
          )}
          {error && <p className="error">{error}</p>}
        </main>

        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            onSend(input);
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
      </div>
    </div>
  );
}
