// Chat tab: message thread + composer. Conversation state lives in the app
// shell so it survives tab switches; this component renders + submits.
import { AskResult, User } from "../../lib/api";
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

export function ChatPage({
  user,
  messages,
  loading,
  error,
  input,
  setInput,
  onSend,
  onOpenSql,
}: {
  user: User;
  messages: ChatMsg[];
  loading: boolean;
  error: string | null;
  input: string;
  setInput: (v: string) => void;
  onSend: (question: string) => void;
  onOpenSql: (sql: string) => void;
}) {
  return (
    <>
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
    </>
  );
}
