// Chat tab: conversations sidebar + message thread + composer. Conversation
// state lives in the app shell so it survives tab switches; the sidebar lists
// past conversations (new conversation = fresh thread, follow-ups in the same
// thread stay multi-turn).
import { useQuery } from "@tanstack/react-query";
import { AskResult, ConversationSummary, getConversations, User } from "../../lib/api";
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

export function ChatPage({
  user,
  messages,
  loading,
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
            <div className="msg assistant">
              <div className="bubble">Agent is working…</div>
            </div>
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
