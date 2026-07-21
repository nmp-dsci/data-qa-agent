// Chat tab. Empty state = hero: greeting + centered composer + suggestion
// cards (the Perplexity ask-moment). Once a thread exists the composer docks
// to the bottom and answers render on the canvas — only the user's message
// keeps a bubble; assistant answers get an identity row and the report pages
// directly on a ~768px reading column (960px when a report is present).
// Conversation state lives in the app shell so it survives tab switches.
import { useEffect, useMemo, useState } from "react";
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
import { useStickToBottom } from "../../lib/useStickToBottom";
import { PageLayout } from "../../report-engine/PageLayout";
import { Composer } from "../../ui/Composer";
import { FlightPath, InstrumentLabel } from "../../ui/flightdeck";
import { BrandMark } from "../../ui/icons";
import { ResultView } from "./ResultView";

export interface ChatMsg {
  role: "user" | "assistant";
  content: string;
  result?: AskResult;
}

/** The flight plan (s25): the four opening questions, numbered as waypoints on
 *  a route rather than scattered as cards. Each row keeps the full question
 *  visible — the point of a flight plan is that you can read the legs. */
const SUGGESTIONS: { title: string; q: string }[] = [
  {
    title: "Price trend",
    q: "show me trend of sale price for houses for Normanhurst vs Hornsby for all time 2010 to 2026",
  },
  { title: "Top movers", q: "What are the top growth suburbs for sale price and rent?" },
  { title: "Rent growth", q: "Which suburbs have the highest rent growth?" },
  { title: "Compare", q: "Top suburbs by sale price growth?" },
];

/** The run, as a sortie (s25). The SSE step actions the agent already emits are
 *  remapped onto five flight phases — no new backend data, just a different
 *  reading of the same stream. Kept as substring matches because the actions
 *  are human-authored strings in sandbox_agent.py, not an enum. */
const FLIGHT_PHASES = [
  { key: "plan", label: "Plan" },
  { key: "sql", label: "SQL" },
  { key: "analyze", label: "Analyze" },
  { key: "compose", label: "Compose" },
  { key: "landed", label: "Landed" },
];

function phaseFor(action: string): number {
  const a = action.toLowerCase();
  if (a.includes("querying data")) return 1;
  if (a.includes("building the report")) return 2;
  if (a.includes("streaming page") || a.includes("concluding")) return 3;
  return 0; // knowledge lookups, schema inspection, value resolution — planning
}

function greeting(name: string): string {
  const h = new Date().getHours();
  const part = h < 12 ? "morning" : h < 18 ? "afternoon" : "evening";
  return `Good ${part}, ${name.trim().split(/\s+/)[0]}`;
}

/** ChatGPT-style date buckets for the sidebar. */
function groupFor(iso: string): string {
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(new Date()) - day(new Date(iso))) / 86_400_000);
  if (Number.isNaN(diff)) return "Older";
  if (diff <= 0) return "Today";
  if (diff === 1) return "Yesterday";
  if (diff < 7) return "Previous 7 days";
  return "Older";
}

const GROUP_ORDER = ["Today", "Yesterday", "Previous 7 days", "Older"];

/** Sidebar meta line (issue #6): a timestamp + turn count under each title, so
 *  otherwise-similar conversations are distinguishable at a glance. */
function convMeta(c: ConversationSummary): string {
  const when = c.last_at ?? c.created_at;
  const d = new Date(when);
  const timeStr = Number.isNaN(d.getTime())
    ? ""
    : d.toDateString() === new Date().toDateString()
      ? d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
      : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const turns = Math.max(1, Math.round((c.message_count || 0) / 2));
  const turnStr = `${turns} ${turns === 1 ? "turn" : "turns"}`;
  return timeStr ? `${turnStr} · ${timeStr}` : turnStr;
}

export function ConversationList({
  activeId,
  onOpen,
  onNew,
}: {
  activeId: string | null;
  onOpen: (id: string) => void;
  onNew: () => void;
}) {
  const q = useQuery({ queryKey: ["conversations"], queryFn: getConversations });
  const [filter, setFilter] = useState("");
  const conversations: ConversationSummary[] = q.data ?? [];
  const groups = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const shown = needle
      ? conversations.filter((c) => (c.title ?? "").toLowerCase().includes(needle))
      : conversations;
    const byGroup = new Map<string, ConversationSummary[]>();
    for (const c of shown) {
      const g = groupFor(c.created_at);
      byGroup.set(g, [...(byGroup.get(g) ?? []), c]);
    }
    return GROUP_ORDER.filter((g) => byGroup.has(g)).map((g) => ({
      label: g,
      items: byGroup.get(g)!,
    }));
  }, [conversations, filter]);

  return (
    <aside className="conv-panel">
      <button className="conv-new" onClick={onNew}>
        + New conversation
      </button>
      <input
        className="conv-search"
        placeholder="Search conversations"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      {q.isLoading &&
        [78, 64, 84, 58, 70].map((w, i) => (
          <div key={i} className="skel conv-skel" style={{ width: `${w}%` }} />
        ))}
      {groups.map((g) => (
        <div key={g.label} className="conv-group">
          <div className="schema-title">{g.label}</div>
          {g.items.map((c) => (
            <button
              key={c.id}
              className={`conv-item${c.id === activeId ? " active" : ""}`}
              onClick={() => onOpen(c.id)}
              title={c.title ?? ""}
            >
              <span className="conv-title">{c.title || "Untitled"}</span>
              <span className="conv-meta">{convMeta(c)}</span>
            </button>
          ))}
        </div>
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

/** Identity row every assistant turn (streamed or final) opens with. */
function AnswerHead({ note }: { note?: string | null }) {
  return (
    <div className="answer-head">
      <BrandMark size={20} />
      <b>Data agent</b>
      {note && <span className="answer-note">{note}</span>}
    </div>
  );
}

/** The streamed answer while the agent works: the running step list, then one
 *  section per planned page — same PageLayout as the final answer, ghosts
 *  until a frame lands, paywall teasers for locked plan entries. */
function WorkingAnswer({
  working,
  elapsedS,
  progress,
  pagePlan,
  streamedPages,
}: {
  working?: string | null;
  elapsedS?: number | null;
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

  // Max, not last: a retry can emit "Querying data" again after the report has
  // started building, and the flight strip must never fly backwards. LANDED
  // lights once every open page slot has a frame — the report is on screen.
  const stepPhase = progress.length ? Math.max(...progress.map((p) => phaseFor(p.action))) : 0;
  const allLanded = open.length > 0 && open.every((s) => streamedPages[s.index] != null);
  const phase = allLanded ? 4 : stepPhase;

  return (
    <div className="answer wide">
      <AnswerHead note={working ?? "working…"} />
      {/* The flagship carry: the same SSE progress, read as a sortie. */}
      <div className="flight-strip">
        <div className="flight-strip-head">
          <InstrumentLabel tone="hud">In flight</InstrumentLabel>
          {elapsedS != null && (
            <span className="flight-strip-clock">
              {String(Math.floor(elapsedS / 60)).padStart(2, "0")}:
              {String(Math.floor(elapsedS % 60)).padStart(2, "0")}
            </span>
          )}
        </div>
        <FlightPath stops={FLIGHT_PHASES} active={phase} />
      </div>
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
              <div className="locked-teaser">{label} pages are available on a higher plan.</div>
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
  );
}

export function ChatPage({
  user,
  messages,
  loading,
  working,
  elapsedS,
  progress,
  pagePlan,
  streamedPages,
  error,
  input,
  setInput,
  onSend,
  onStop,
  onOpenSql,
  onPromoteToGolden,
  conversationId,
  onOpenConversation,
  onNewConversation,
}: {
  user: User;
  messages: ChatMsg[];
  loading: boolean;
  working?: string | null;
  elapsedS?: number | null;
  progress: AskProgress[];
  pagePlan: PagePlanSlot[];
  streamedPages: Record<number, PageFrame>;
  error: string | null;
  input: string;
  setInput: (v: string) => void;
  onSend: (question: string) => void;
  onStop?: () => void;
  onOpenSql: (sql: string) => void;
  onPromoteToGolden?: (goldenId: string) => void;
  conversationId: string | null;
  onOpenConversation: (id: string) => void;
  onNewConversation: () => void;
}) {
  // The thread follows the stream while the user is at the bottom; scrolling up
  // pauses following and shows the jump pill. Sending or opening a conversation
  // always snaps to the newest content.
  const { ref: mainRef, pinned, scrollToBottom } = useStickToBottom();
  useEffect(() => {
    scrollToBottom();
  }, [conversationId, scrollToBottom]);
  useEffect(() => {
    if (loading) scrollToBottom("smooth");
  }, [loading, scrollToBottom]);

  const empty = messages.length === 0 && !loading;

  return (
    <div className="chat-layout">
      <ConversationList
        activeId={conversationId}
        onOpen={onOpenConversation}
        onNew={onNewConversation}
      />
      <div className="chat-main">
        {empty ? (
          <div className="hero-wrap">
            <div className="hero">
              <h1>{greeting(user.display_name)}</h1>
              {/* The data source, read as an instrument line rather than prose. */}
              <p className="hero-sub instrument-label dim">
                Sales &amp; rents · NSW suburbs · 2010–2026 · governed SQL
              </p>
              <Composer
                value={input}
                onChange={setInput}
                onSend={onSend}
                busy={loading}
                placeholder="Ask about your data…"
                autoFocus
              />
              <div className="flight-plan">
                <InstrumentLabel tone="dim" className="flight-plan-head">
                  Flight plan
                </InstrumentLabel>
                <div className="sugs">
                  {SUGGESTIONS.map((s, i) => (
                    <button key={s.title} className="sug" onClick={() => onSend(s.q)}>
                      <span className="sug-n">{String(i + 1).padStart(2, "0")}</span>
                      <span className="sug-text">
                        <b>{s.title}</b>
                        <span>{s.q}</span>
                      </span>
                    </button>
                  ))}
                </div>
              </div>
              <p className="onboard-hint">
                Answers open with a <b>Summary</b> and an <b>Insights</b> page. Click any element
                to leave feedback · <kbd>⌘K</kbd> opens the command palette.
              </p>
            </div>
          </div>
        ) : (
          <>
            <main ref={mainRef}>
              {messages.map((m, i) =>
                m.role === "user" ? (
                  <div key={i} className="msg user">
                    <div className="bubble">{m.content}</div>
                  </div>
                ) : (
                  <div
                    key={i}
                    className={`answer${
                      m.result?.report || (m.result?.pages?.length ?? 0) > 0 ? " wide" : ""
                    }`}
                  >
                    <AnswerHead />
                    <div className="content">{m.content}</div>
                    {m.result && (
                      <ResultView
                        result={m.result}
                        isAdmin={user.role === "admin"}
                        onOpenSql={onOpenSql}
                        onPromoteToGolden={onPromoteToGolden}
                      />
                    )}
                  </div>
                ),
              )}
              {loading && (
                <WorkingAnswer
                  working={working}
                  elapsedS={elapsedS}
                  progress={progress}
                  pagePlan={pagePlan}
                  streamedPages={streamedPages}
                />
              )}
              {error && <p className="error">{error}</p>}
            </main>

            {!pinned && (
              <button
                type="button"
                className="jump-latest"
                onClick={() => scrollToBottom("smooth")}
              >
                ↓ Jump to latest
              </button>
            )}

            <div className="dock">
              <Composer
                value={input}
                onChange={setInput}
                onSend={onSend}
                onStop={onStop}
                busy={loading}
                placeholder="Ask a follow-up…"
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
