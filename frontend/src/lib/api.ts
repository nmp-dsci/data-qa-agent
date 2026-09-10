const API = (import.meta.env.VITE_API_URL as string) ?? "http://localhost:8000";

// s29: while Aurora Serverless resumes from auto-pause the backend answers
// 503 db_warming (a classified connect failure, not a real error). The resume
// took ~31s in the observed prod session, so the retry window must comfortably
// cover it. Shared by the transport-level retry below and the login exchange
// (lib/auth.ts exchangeCredential), which narrates the same wait on the card.
export const WARMING_MAX_MS = 75_000;
export const WARMING_RETRY_MS = 4_000;

async function isWarmingResponse(resp: Response): Promise<boolean> {
  if (resp.status !== 503) return false;
  try {
    const body: unknown = await resp.clone().json();
    return (body as { detail?: unknown })?.detail === "db_warming";
  } catch {
    return false;
  }
}

// Every backend call goes through this so the dev-auth session cookie
// (services/backend-api/app/routers/auth.py::dev_login) rides along. Cross-port
// requests (5230 -> 8000) do not send cookies without an explicit
// `credentials: "include"` — the browser default is "same-origin", which would
// silently drop it. Harmless when there is no cookie to send (Google mode,
// prod): the in-memory bearer token in authHeaders() keeps working exactly as
// before either way.
//
// Every endpoint fails with 503 db_warming while Aurora resumes — not just the
// login exchange — so the wait-it-out retry lives here: a mid-session Ask /
// Explore / SQL call rides out the wake behind its surface's existing pending
// UI instead of surfacing a raw 503 the user has to retry by hand. Safe for
// POSTs too: db_warming means the connect failed, so nothing executed. getMe
// opts out — the login exchange owns that retry so it can narrate progress on
// the card. An abort wakes the sleep early; the next fetch then rejects with
// the caller's AbortError as usual.
async function apiFetch(
  input: string,
  init?: RequestInit,
  { retryWarming = true }: { retryWarming?: boolean } = {},
): Promise<Response> {
  const started = Date.now();
  let waited = false;
  for (;;) {
    const resp = await fetch(input, { ...init, credentials: "include" });
    if (!retryWarming || !(await isWarmingResponse(resp))) {
      // s32 W0: an Aurora cold start is a real operational event, but the
      // backend can't log it — the database it would log to is the thing that
      // was asleep. So the client reports it once the wake completes, which is
      // the first moment a write can actually land. This feeds the deck's
      // cold-start counter (Tier 1, zero AWS calls).
      if (waited) reportWarming(input, Date.now() - started);
      return resp;
    }
    if (Date.now() - started >= WARMING_MAX_MS) return resp;
    waited = true;
    await new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, WARMING_RETRY_MS);
      const onAbort = () => {
        clearTimeout(timer);
        resolve();
      };
      if (init?.signal?.aborted) onAbort();
      else init?.signal?.addEventListener("abort", onAbort, { once: true });
    });
  }
}

/** Report a completed Aurora wake to the event stream (s32 W0).
 *
 *  Declared before apiFetch uses it but defined with `track` below, which is
 *  fine — function declarations hoist. Deliberately never awaited and never
 *  retried: if this one write fails the counter under-reports by one, which is
 *  strictly better than a telemetry call that can block a real request.
 */
function reportWarming(input: string, waitedMs: number): void {
  // Strip the origin so the payload carries a path, not a full URL.
  const path = input.replace(/^https?:\/\/[^/]+/, "");
  track("db_warming", { path, waited_ms: waitedMs });
}

export interface User {
  id: string;
  username: string;
  email: string;
  display_name: string;
  role: string;
}

export interface AuthConfig {
  auth_mode: "dev" | "google" | "demo";
  client_id?: string | null;
  scopes: string[];
}

export interface AgentToolCall {
  name: string;
  args: string;
  tool_call_id?: string | null;
}

export interface AgentDecision {
  order?: number;
  type: string;
  choice?: string | null;
  why?: string | null;
  status?: string | null;
  row_count?: number | null;
  sql?: string | null;
}

export interface AgentStep {
  // Message-history trace: system | user | model | tool_return | retry.
  // Legacy hand-built kinds (sql/chart/memory/analytics/knowledge) still render.
  kind: "system" | "user" | "model" | "tool_return" | "retry" | string;
  content?: string;
  // model steps
  tool_calls?: AgentToolCall[];
  thinking?: string | null;
  model_name?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  total_tokens?: number | null;
  // tool_return / retry
  name?: string | null;
  tool_call_id?: string | null;
  // legacy hand-built step fields (kept for back-compat with old stored traces)
  status?: string;
  attempt?: number;
  sql?: string;
  row_count?: number;
  error?: string;
  mark?: string;
  title?: string | null;
  fact?: string;
  intent?: string;
  decisions?: AgentDecision[];
}

export interface Headline {
  element_id: string;
  label: string;
  value: string;
  basis: string;
  related: boolean;
  query_ref: string | null;
}

export interface Insight {
  element_id: string;
  heading: string;
  body: string;
  query_refs: string[];
  chart: Record<string, unknown> | null;
}

export interface Profile {
  element_id: string;
  heading: string;
  body: string;
  query_refs: string[];
  chart: Record<string, unknown> | null;
}

export interface QueryRef {
  element_id: string;
  ref: string;
  purpose: string;
  sql: string | null;
  columns: string[];
  rows: unknown[][];
  row_count: number;
}

export interface InsightReport {
  element_id: string;
  summary: string;
  headlines: Headline[];
  insights: Insight[];
  profiles: Profile[];
  main_chart: Record<string, unknown> | null;
  queries: QueryRef[];
  knowledge_pages_used: string[];
  knowledge_version: string;
}

// s46: the Slides/Sheets artifact the agent builds per answer — replaces the
// in-browser report-engine rendering (page objects/charts). embed_url is a
// chrome-free viewer suitable for an iframe; deck_url/sheet_url are the full
// editors the "open in" buttons link out to.
export interface ArtifactSlide {
  index: number;
  layout: string;
  headline: string;
  has_chart: boolean;
  has_table: boolean;
  has_kpi: boolean;
  sheet_tab: string | null;
  rows: number;
}

export interface Artifact {
  deck_url: string;
  embed_url: string;
  sheet_url: string;
  presentation_id: string;
  spreadsheet_id: string;
  slides: ArtifactSlide[];
}

export interface AskResult {
  conversation_id: string;
  message_id: string;
  run_id: string;
  answer: string;
  sql: string | null;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  chart: Record<string, unknown> | null;
  engine: string;
  input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: number | null;
  /** s32 W1: the answer came back, but not the one that was asked for — a
   *  retried-then-stubbed run or an unreachable agent. Optional so history
   *  replays (which predate the field) type-check unchanged. */
  degraded?: boolean;
  steps: AgentStep[];
  /** s46: the Google Slides/Sheets artifact this answer built. Null/absent for
   *  pre-artifact answers (history replays, demo pack entries recorded
   *  earlier) — the UI falls back to the plain SQL/rows view. */
  artifact?: Artifact | null;
  /** s38 demo mode: free text fuzzy-matched this recorded question rather than
   *  hitting it exactly — the bubble shows a "closest recorded answer" note. */
  demo_matched_question?: string | null;
}

export interface AdminFeedback {
  id: string;
  rating: number;
  accurate: boolean | null;
  issue_flag: boolean;
  comment: string | null;
  target_kind: string;
  target_ref: string;
  target_snapshot: Record<string, unknown>;
  target_render_html: string | null;
  report_snapshot: InsightReport | null;
  client_context: Record<string, unknown>;
  knowledge_version: string;
  knowledge_pages: string[];
  scope: string;
  status: string;
  created_at: string;
  username: string;
  message_id: string;
  report: InsightReport | null;
  question: string | null;
}

export interface EvalCase {
  id: string;
  question: string;
  expectation: string;
  target_kind: string;
  knowledge_version: string;
  status: string;
  stale_cycles: number;
  created_at: string;
  updated_at: string;
}

export interface AdminEvent {
  id: string;
  event_type: string;
  created_at: string;
  payload: Record<string, unknown>;
  username: string | null;
}

export interface ConfigItem {
  key: string;
  value: string;
  note: string | null;
  secret: boolean;
}

export interface ConfigSection {
  title: string;
  service: string;
  items: ConfigItem[];
  error: string | null;
}

export interface AdminConfig {
  sections: ConfigSection[];
}

export interface AdminUser {
  id: string;
  username: string;
  email: string;
  display_name: string;
  role: string;
  last_active: string | null;
}

export interface AdminDataset {
  id: string;
  slug: string;
  name: string;
  status: string;
  row_count: number;
  access_count: number;
}

export interface AdminQueryRun {
  id: string;
  created_at: string;
  username: string;
  dataset: string | null;
  engine: string;
  source: string;
  channel: string;
  row_count: number;
  latency_ms: number | null;
  status: string;
  question: string | null;
  sql_text: string | null;
  error: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  trace: AgentStep[] | null;
}

export interface SqlRunResult {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  latency_ms: number | null;
  engine: string;
  error: string | null;
}

export interface SqlHistoryItem {
  id: string;
  created_at: string;
  sql_text: string | null;
  row_count: number;
  latency_ms: number | null;
  status: string;
  error: string | null;
}

export type AiAction = "generate" | "explain" | "fix" | "optimize";

export interface AiAssistResult {
  sql: string | null;
  explanation: string | null;
  engine: string;
  error: string | null;
}

export interface CatalogColumn {
  name: string;
  type: string | null;
  description: string | null;
}

export interface CatalogTable {
  schema: string;
  table: string;
  description: string | null;
  columns: CatalogColumn[];
}

let token: string | null = null;
let sessionId = Math.random().toString(36).slice(2);

// s38 analytics: a random, anonymous browser identity. localStorage survives
// for months, which is exactly what makes "returning visitor" countable; it
// identifies a browser, never a person (no fingerprinting — a cleared store or
// an incognito window is simply a new visitor). Falls back to a per-load id
// where storage is blocked, so that visit counts as unique-but-unrepeatable.
const VISITOR_KEY = "dp_visitor_id";
let visitorIdCache: string | null = null;

function visitorId(): string {
  if (visitorIdCache) return visitorIdCache;
  let id: string | null = null;
  try {
    id = localStorage.getItem(VISITOR_KEY);
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem(VISITOR_KEY, id);
    }
  } catch {
    id = Math.random().toString(36).slice(2);
  }
  visitorIdCache = id;
  return id;
}

export function setToken(t: string | null) {
  token = t;
}

// Marks every request as coming from the web app so query runs are attributed
// to the 'web' channel in app.query_runs (a direct API hit has no such header
// and is recorded as 'api'). Sent on all requests; the backend only reads it
// where it audits a run (/ask, /sql) and as the fence on the /health/db wake
// probe (see wakeDb).
function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = { "X-Client-Channel": "web" };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

export async function getAuthConfig(): Promise<AuthConfig> {
  const resp = await apiFetch(`${API}/auth/config`);
  if (!resp.ok) throw new Error(`Could not load auth config (${resp.status})`);
  return resp.json();
}

export async function devLogin(username: string): Promise<{ access_token: string; user: User }> {
  const resp = await apiFetch(`${API}/auth/dev-login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username }),
  });
  if (!resp.ok) throw new Error(`Login failed (${resp.status})`);
  return resp.json();
}

/** s38: the walk-in demo door — no body, no account; the backend mints the
 *  seeded demo user's session. 404s outside demo mode. */
export async function demoLogin(): Promise<{ access_token: string; user: User }> {
  const resp = await apiFetch(`${API}/auth/demo-login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!resp.ok) throw new Error(`Could not enter the demo (${resp.status})`);
  return resp.json();
}

export interface DemoQuestion {
  id: string;
  question: string;
}

/** The recorded questions the demo can answer — feeds the chat chip rail. */
export async function getDemoQuestions(): Promise<DemoQuestion[]> {
  const resp = await apiFetch(`${API}/demo/questions`);
  if (!resp.ok) return [];
  return resp.json();
}

// Clears the httpOnly session cookie set by devLogin. The frontend cannot do
// this itself with document.cookie — httpOnly means JS never sees the cookie
// at all — so sign-out has to be a round trip. Best-effort: a failed logout
// call should not block the client-side sign-out (clearing the in-memory
// token, resetting the UI), so callers swallow the error rather than surface it.
export async function logoutSession(): Promise<void> {
  await apiFetch(`${API}/auth/logout`, { method: "POST" });
}

// Carries the HTTP status + backend detail string so callers can tell a
// retryable condition (503 db_warming while Aurora resumes, s29) from a real
// failure. message stays the human-readable line the UI already showed.
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function errorDetail(resp: Response): Promise<string | null> {
  try {
    const body: unknown = await resp.json();
    const detail = (body as { detail?: unknown })?.detail;
    return typeof detail === "string" ? detail : null;
  } catch {
    return null;
  }
}

export async function getMe(): Promise<User> {
  const resp = await apiFetch(`${API}/me`, { headers: authHeaders() }, { retryWarming: false });
  if (!resp.ok) {
    throw new ApiError(`Could not load profile (${resp.status})`, resp.status, await errorDetail(resp));
  }
  return resp.json();
}

// s29 F3: fired from the login card on mount so Aurora starts resuming while
// the user is still in the Google sign-in dance. Fire-and-forget — a "waking"
// answer (or any failure) is the expected cold-visit case, not actionable.
// The channel marker in authHeaders is required: without it the endpoint
// answers without touching (waking) the database, so a generic poller pointed
// at it cannot hold Aurora awake.
export function wakeDb(): void {
  apiFetch(`${API}/health/db`, { headers: authHeaders() }).catch(() => {});
}

// s32 W1: a client-side ceiling on one answer. The backend already retries the
// agent hop and degrades rather than 502-ing, but a request can still hang
// somewhere neither side controls (a proxy, a dead socket that never resets), and
// a spinner with no end is the worst failure mode there is. Generous: prod's
// full-answer p95 is ~96s, so this only fires when something is genuinely stuck.
export const ASK_CEILING_MS = 240_000;

/** An AbortSignal that fires when `signal` aborts OR the ceiling elapses.
 *
 *  Returns a disposer the caller must run, so a completed answer doesn't leave a
 *  4-minute timer alive — over a chat session that would accumulate one per
 *  question. */
function withCeiling(signal: AbortSignal | undefined, ms: number) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(new Error("ask-ceiling")), ms);
  const onAbort = () => ctrl.abort(signal?.reason);
  if (signal?.aborted) onAbort();
  else signal?.addEventListener("abort", onAbort, { once: true });
  return {
    signal: ctrl.signal,
    /** True when WE aborted on the ceiling, not the user pressing Stop. */
    timedOut: () => !signal?.aborted && ctrl.signal.aborted,
    dispose: () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    },
  };
}

/** s38: stable backend detail strings -> sentences a visitor should read. */
export function friendlyDetail(detail: string | null, fallback: string): string {
  switch (detail) {
    case "demo_full":
      return "The demo is at capacity right now — try again in a few seconds.";
    case "demo_rate_limited":
      return "Easy on the throttle — the demo rate-limits requests. Try again shortly.";
    case "not_available_demo":
      return "Not available in this demo — it runs a live LLM in the full build.";
    default:
      return fallback;
  }
}

function ceilingError(): ApiError {
  return new ApiError(
    `That took longer than ${Math.round(ASK_CEILING_MS / 1000)}s with no answer. ` +
      "The run may still finish server-side — try asking again.",
    408,
    "ask_ceiling",
  );
}

export async function ask(
  question: string,
  conversationId: string | null,
  signal?: AbortSignal,
): Promise<AskResult> {
  const ceiling = withCeiling(signal, ASK_CEILING_MS);
  try {
    const resp = await apiFetch(`${API}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ question, conversation_id: conversationId }),
      signal: ceiling.signal,
    });
    if (!resp.ok) {
      const detail = (await errorDetail(resp)) ?? `HTTP ${resp.status}`;
      throw new ApiError(
        friendlyDetail(detail, `Ask failed (${resp.status}): ${detail}`),
        resp.status,
        detail,
      );
    }
    return resp.json();
  } catch (e) {
    if (ceiling.timedOut()) throw ceilingError();
    throw e;
  } finally {
    ceiling.dispose();
  }
}

export interface AskStatus {
  state: string;
  elapsed_s?: number;
  // s40 queue mode: admission position when the job is waiting for a worker,
  // and deliveries when a dead worker's job was restarted on another one.
  position?: number;
  deliveries?: number;
}

/** A live agent step while the answer is being built (running step list). */
export interface AskProgress {
  n: number;
  action: string;
  detail?: string;
}

// s10 streaming pages: the `plan` frame declares up front how many pages this
// answer will complete for this user (locked = paywall teaser for pages above
// their plan); one `page` frame then arrives per finished page as a status
// marker only (s46 removed the rendered page-object payload). `result` stays
// authoritative once it lands.
export type PageSlotStatus = "planned" | "building" | "complete" | "skipped" | "locked";

export interface PagePlanSlot {
  index: number;
  kind: string; // summary | insights | opportunities
  status: PageSlotStatus;
}

export interface PageFrame {
  index: number;
  kind?: string;
  status: string; // complete | skipped
}

/** SSE variant of ask(): live status + step progress while the agent works,
 *  the page plan + each finished page as it streams, then the result. Falls
 *  back to plain ask() if the stream can't be established. */
export async function askStream(
  question: string,
  conversationId: string | null,
  onStatus: (s: AskStatus) => void,
  onProgress?: (p: AskProgress) => void,
  onPlan?: (slots: PagePlanSlot[]) => void,
  onPage?: (frame: PageFrame) => void,
  signal?: AbortSignal,
): Promise<AskResult> {
  // Same ceiling as ask() — a stream that stops producing frames must not leave
  // the composer disabled forever (s32 W1). The agent's 2s heartbeats mean a
  // healthy stream never approaches it.
  const ceiling = withCeiling(signal, ASK_CEILING_MS);
  try {
    return await askStreamInner(
      question,
      conversationId,
      onStatus,
      onProgress,
      onPlan,
      onPage,
      ceiling.signal,
      signal,
    );
  } catch (e) {
    if (ceiling.timedOut()) throw ceilingError();
    throw e;
  } finally {
    ceiling.dispose();
  }
}

async function askStreamInner(
  question: string,
  conversationId: string | null,
  onStatus: (s: AskStatus) => void,
  onProgress: ((p: AskProgress) => void) | undefined,
  onPlan: ((slots: PagePlanSlot[]) => void) | undefined,
  onPage: ((frame: PageFrame) => void) | undefined,
  signal: AbortSignal,
  /** The caller's own signal — only a user-initiated Stop skips the fallback. */
  userSignal: AbortSignal | undefined,
): Promise<AskResult> {
  let resp: Response;
  try {
    resp = await apiFetch(`${API}/ask/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ question, conversation_id: conversationId }),
      signal,
    });
  } catch (e) {
    // A user-initiated stop (or the ceiling) must not fall back to blocking ask().
    if (userSignal?.aborted || signal.aborted) throw e;
    return ask(question, conversationId, signal);
  }
  if (!resp.ok || !resp.body) return ask(question, conversationId, signal);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE frames are separated by a blank line.
    for (;;) {
      const sep = buffer.indexOf("\n\n");
      if (sep === -1) break;
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const eventLine = frame.split("\n").find((l) => l.startsWith("event: "));
      const dataLine = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!eventLine || !dataLine) continue;
      const event = eventLine.slice(7).trim();
      const data = dataLine.slice(6);
      if (event === "status") {
        try {
          onStatus(JSON.parse(data) as AskStatus);
        } catch {
          /* ignore malformed status frames */
        }
      } else if (event === "progress") {
        try {
          onProgress?.(JSON.parse(data) as AskProgress);
        } catch {
          /* ignore malformed progress frames */
        }
      } else if (event === "plan") {
        try {
          const parsed = JSON.parse(data) as { pages?: PagePlanSlot[] };
          if (Array.isArray(parsed.pages)) onPlan?.(parsed.pages);
        } catch {
          /* ignore malformed plan frames */
        }
      } else if (event === "page") {
        try {
          onPage?.(JSON.parse(data) as PageFrame);
        } catch {
          /* ignore malformed page frames */
        }
      } else if (event === "result") {
        return JSON.parse(data) as AskResult;
      } else if (event === "error") {
        let detail = data;
        try {
          detail = (JSON.parse(data) as { detail?: string }).detail ?? data;
        } catch {
          /* keep raw */
        }
        throw new Error(`Ask failed: ${detail}`);
      }
    }
  }
  throw new Error("Ask stream ended without a result");
}

export async function runSql(sql: string): Promise<SqlRunResult> {
  const resp = await apiFetch(`${API}/sql`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ sql }),
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`Run failed (${resp.status}): ${detail}`);
  }
  return resp.json();
}

export async function getSqlHistory(limit = 20): Promise<SqlHistoryItem[]> {
  const resp = await apiFetch(`${API}/sql/history?limit=${limit}`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`Could not load history (${resp.status})`);
  return resp.json();
}

export async function runSqlAi(
  action: AiAction,
  args: { prompt?: string; sql?: string },
): Promise<AiAssistResult> {
  const resp = await apiFetch(`${API}/sql/ai`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ action, prompt: args.prompt ?? null, sql: args.sql ?? null }),
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`AI assist failed (${resp.status}): ${detail}`);
  }
  return resp.json();
}

export async function getCatalog(): Promise<CatalogTable[]> {
  const resp = await apiFetch(`${API}/schema/catalog`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`Could not load schema (${resp.status})`);
  const data = (await resp.json()) as { tables: CatalogTable[] };
  return data.tables ?? [];
}

// ---------------------------------------------------------------------------
// Explore (s19) — dataset profiling, trends, dictionary + NL setup.
// ---------------------------------------------------------------------------
export interface DomainValue {
  value: string | number;
  count: number;
}

export interface ExploreDimension {
  name: string;
  label: string;
  kind: "categorical" | "ordinal" | "time" | "geo";
  source: "mart" | "geo" | "computed";
  ordinal: boolean;
  unit: string | null;
  domain?: DomainValue[] | null;
  typeahead?: boolean;
  /** Multi-selectable (IN filter) — categorical/geo dims. Year/FY are single. */
  multi?: boolean;
}

export interface ExploreMetric {
  name: string;
  label: string;
  format: "currency" | "number" | "percent";
  kind: "additive" | "derived";
  /** For a plain ratio-of-sums, the two additive legs behind it — what a client
   *  needs to recompose the metric as a weighted average at its own grain.
   *  Null for metrics that aren't a simple num/den (they can't be). */
  num?: string | null;
  den?: string | null;
}

export interface ExploreGeo {
  dimension: string;
  layer: string;
}

export interface ExploreDataset {
  slug: string;
  name: string;
  time_dim: string;
  default_metric: string;
  geo: ExploreGeo | null;
  dimensions: ExploreDimension[];
  metrics: ExploreMetric[];
  time_range?: { min: string | number | null; max: string | number | null };
}

export type ExploreFilterValue =
  | string
  | number
  | (string | number)[]
  | { min?: string | number; max?: string | number };
export type ExploreFilters = Record<string, ExploreFilterValue>;

export interface AggregateResult {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  latency_ms: number;
  sql?: string;
}

export interface ProfileSegment {
  value: string;
  target: number | null;
  comparison: number | null;
  delta: number | null;
  delta_pct: number | null;
  target_n: number | null;
}

export interface ProfilePredictor {
  predictor: string;
  label: string;
  kind: string;
  ordinal: boolean;
  signal: number;
  segments: ProfileSegment[];
}

export interface ProfileMetricDelta {
  metric: string;
  label: string;
  fmt: string;
  target: number | null;
  comparison: number | null;
  delta: number | null;
  delta_pct: number | null;
}

export interface ProfileResult {
  dataset: string;
  /** The dataset each cohort was actually profiled against — equal to `dataset`
   *  for a same-dataset comparison, different for a cross-dataset one. */
  target_dataset: string;
  comparison_dataset: string;
  metric: string;
  metric_label: string;
  metric_format: string;
  /** The metric each cohort was actually measured on — equal to `metric` when
   *  Target and Comparison share one, different when they were chosen
   *  independently (e.g. Sold volume vs Bond volume). */
  target_metric: string;
  comparison_metric: string;
  target_metric_label: string;
  comparison_metric_label: string;
  target_metric_format: string;
  comparison_metric_format: string;
  calculation: "raw" | "pct_total" | "growth";
  target_pct_total?: number | null;
  comparison_pct_total?: number | null;
  target_total: number | null;
  comparison_total: number | null;
  delta: number | null;
  delta_pct: number | null;
  metric_deltas: ProfileMetricDelta[];
  predictors: ProfilePredictor[];
  positive_uplifts: Record<string, unknown>[];
  negative_uplifts: Record<string, unknown>[];
  target_filters: ExploreFilters;
  comparison_filters: ExploreFilters;
  geo: ExploreGeo | null;
  /** The result assembled server-side as page objects (app/explore/pages_builder.py,
   *  unchanged by s46 — Explore never got an artifact, it just lost its chart
   *  renderer). Loosely typed since the removed PageObjectType/Page union no
   *  longer exists on the frontend: the UI reads whatever tabular data each
   *  object carries (data.rows) rather than interpreting it as a chart. */
  pages?: Record<string, unknown>[];
}

export interface AskState {
  mode: "profile" | "trends";
  state: Record<string, unknown>;
}

export async function getExploreDatasets(): Promise<ExploreDataset[]> {
  const resp = await apiFetch(`${API}/explore/datasets`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`Could not load datasets (${resp.status})`);
  const data = (await resp.json()) as { datasets: ExploreDataset[] };
  return data.datasets ?? [];
}

export async function exploreAggregate(body: {
  dataset: string;
  metrics: string[];
  group_by?: string[];
  filters?: ExploreFilters;
  limit?: number;
}): Promise<AggregateResult> {
  const resp = await apiFetch(`${API}/explore/aggregate`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ group_by: [], filters: {}, ...body }),
  });
  if (!resp.ok) throw new Error(`Aggregate failed (${resp.status}): ${await resp.text()}`);
  return resp.json();
}

export async function exploreProfile(body: {
  /** Shared fallback when a cohort doesn't set its own `dataset`/`metric`. */
  dataset?: string;
  metric?: string | null;
  /** How the two cohort values are framed: plain values, each as % of its own
   *  dataset's unfiltered grand total, or target-vs-comparison % change. */
  calculation?: "raw" | "pct_total" | "growth";
  target: { dataset?: string; metric?: string; filters: ExploreFilters };
  comparison: { dataset?: string; metric?: string; filters: ExploreFilters };
}): Promise<ProfileResult> {
  const resp = await apiFetch(`${API}/explore/profile`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`Profile failed (${resp.status}): ${await resp.text()}`);
  return resp.json();
}

export async function exploreAsk(
  question: string,
  mode: "profile" | "trends",
  dataset?: string,
): Promise<AskState> {
  const resp = await apiFetch(`${API}/explore/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ question, mode, dataset: dataset ?? null }),
  });
  if (!resp.ok) throw new Error(`Ask failed (${resp.status}): ${await resp.text()}`);
  return resp.json();
}

export async function exploreTypeahead(
  dataset: string,
  dimension: string,
  q: string,
): Promise<(string | number)[]> {
  const params = new URLSearchParams({ dataset, dimension, q });
  const resp = await apiFetch(`${API}/explore/typeahead?${params}`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`Typeahead failed (${resp.status})`);
  const data = (await resp.json()) as { values: (string | number)[] };
  return data.values ?? [];
}

async function adminGet<T>(path: string): Promise<T> {
  const resp = await apiFetch(`${API}${path}`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`Admin request failed (${resp.status})`);
  return resp.json();
}

/* ---------------------------------------------------------------------------
 * Visitor analytics (s38 P2.5) — the admin-only Analytics tab's data.
 * ------------------------------------------------------------------------- */

export interface AnalyticsSummary {
  days: number;
  totals: {
    visitors: number;
    today_visitors: number;
    sessions: number;
    events: number;
    returning_visitors: number;
  };
  funnel: { event: string; label: string; visitors: number }[];
  daily: { day: string; events: number; visitors: number }[];
  top_events: { event_type: string; count: number }[];
  top_questions: { question: string; count: number; engine: string | null }[];
  recent_sessions: {
    session_id: string;
    started: string;
    last_seen: string;
    events: number;
    event_types: string;
  }[];
}

export async function getAnalyticsSummary(days = 14): Promise<AnalyticsSummary> {
  return adminGet<AnalyticsSummary>(`/analytics/summary?days=${days}`);
}

/* ---------------------------------------------------------------------------
 * Handover analytics (s48 §7) — did anyone open the deck we handed them, and
 * what did they change? Fed by scripts/handover_poll.py.
 * ------------------------------------------------------------------------- */

export interface HandoverAnalytics {
  days: number;
  decks: number;
  opened: number;
  edited: number;
  edit_rate: number | null;
  median_minutes_to_first_edit: number | null;
  edits_by_event: { event: string; edits: number }[];
  edits_by_layout: {
    layout_id: string;
    decks: number;
    edits: number;
    headline_edits: number;
    chart_edits: number;
  }[];
  layout_usage: { layout_id: string; slides: number }[];
  recent: {
    run_id: string;
    question: string | null;
    deck_url: string | null;
    edits: number;
    last_edit_at: string | null;
  }[];
}

export async function getHandoverAnalytics(days = 30): Promise<HandoverAnalytics> {
  return adminGet<HandoverAnalytics>(`/analytics/handover?days=${days}`);
}

function adminListQuery(params?: { limit?: number; since?: string }): string {
  const qs = new URLSearchParams();
  if (params?.limit != null) qs.set("limit", String(params.limit));
  if (params?.since) qs.set("since", params.since);
  const s = qs.toString();
  return s ? `?${s}` : "";
}

export function getAdminEvents(params?: { limit?: number; since?: string }): Promise<AdminEvent[]> {
  return adminGet<AdminEvent[]>(`/admin/events${adminListQuery(params)}`);
}

export function getAdminUsers(): Promise<AdminUser[]> {
  return adminGet<AdminUser[]>("/admin/users");
}

export function getAdminDatasets(): Promise<AdminDataset[]> {
  return adminGet<AdminDataset[]>("/admin/datasets");
}

export function getAdminQueryRuns(params?: {
  limit?: number;
  since?: string;
}): Promise<AdminQueryRun[]> {
  return adminGet<AdminQueryRun[]>(`/admin/query-runs${adminListQuery(params)}`);
}

export function getAdminConfig(): Promise<AdminConfig> {
  return adminGet<AdminConfig>("/admin/config");
}

export interface AgentConfigEntry {
  kind: string;
  name: string;
  title: string;
  description: string;
  spec: Record<string, unknown>;
  demo: Record<string, unknown>;
}

export interface AgentConfigResponse {
  templates: AgentConfigEntry[];
  charts: AgentConfigEntry[];
}

export function getAdminAgentConfig(): Promise<AgentConfigResponse> {
  return adminGet<AgentConfigResponse>("/admin/agent-config");
}

// --- Conversations (Chat history sidebar) ---

export interface ConversationSummary {
  id: string;
  title: string | null;
  created_at: string;
  last_at: string | null;
  message_count: number;
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sql_generated: string | null;
  // s46: only `artifact` is read out of the stored blob — a reopened thread
  // restores the Slides/Sheets deck the same way it used to restore report
  // pages. Older stored shapes may still carry other legacy fields; the UI no
  // longer renders them.
  report: { artifact?: Artifact | null } | null;
  created_at: string;
  // Joined from the message's latest query_run so a reopened thread restores
  // the same result meta an in-session answer shows. `steps` is admin-only
  // (empty otherwise); the rest may be null for pre-audit / legacy messages.
  run_id: string | null;
  engine: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: number | null;
  steps: AgentStep[];
}

export async function getConversations(): Promise<ConversationSummary[]> {
  const resp = await apiFetch(`${API}/conversations`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`Could not load conversations (${resp.status})`);
  return resp.json();
}

export async function getConversationMessages(id: string): Promise<ConversationMessage[]> {
  const resp = await apiFetch(`${API}/conversations/${id}/messages`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`Could not load conversation (${resp.status})`);
  return resp.json();
}

// --- Profile / Settings ---

export interface UserMemory {
  id: string;
  kind: string | null;
  content: string;
  created_at: string;
  last_used_at: string | null;
}

export interface MyAccess {
  role: string;
  rls_note: string;
  datasets: { slug: string; name: string; status: string; access: string }[];
}

export async function getMyMemories(): Promise<UserMemory[]> {
  const resp = await apiFetch(`${API}/me/memories`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`Could not load memories (${resp.status})`);
  return resp.json();
}

export async function deleteMyMemory(id: string): Promise<{ deleted: boolean }> {
  const resp = await apiFetch(`${API}/me/memories/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!resp.ok) throw new Error(`Could not delete memory (${resp.status})`);
  return resp.json();
}

export async function getMyAccess(): Promise<MyAccess> {
  const resp = await apiFetch(`${API}/me/access`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`Could not load access (${resp.status})`);
  return resp.json();
}

// ---- Service accounts (s35) -----------------------------------------------
// Machine identities for the non-UI surfaces. Note what the list type does NOT
// carry: there is no `key` field, because the secret half is unrecoverable by
// design — only the public key_id is ever readable after minting.
export interface ServiceAccount {
  id: string;
  name: string;
  surface: string;
  key_id: string;
  username: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

/** The create response — the ONLY time `key` exists anywhere. */
export interface ServiceAccountCreated {
  id: string;
  name: string;
  surface: string;
  key_id: string;
  username: string;
  key: string;
}

export async function listServiceAccounts(): Promise<ServiceAccount[]> {
  const resp = await apiFetch(`${API}/admin/service-accounts`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`Could not load service accounts (${resp.status})`);
  return resp.json();
}

export async function createServiceAccount(input: {
  name: string;
  surface: string;
  dataset_slugs: string[];
}): Promise<ServiceAccountCreated> {
  const resp = await apiFetch(`${API}/admin/service-accounts`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(input),
  });
  if (!resp.ok) throw new Error(`Could not create the key (${resp.status})`);
  return resp.json();
}

export async function revokeServiceAccount(id: string): Promise<{ id: string }> {
  const resp = await apiFetch(`${API}/admin/service-accounts/${id}/revoke`, {
    method: "POST",
    headers: authHeaders(),
  });
  if (!resp.ok) throw new Error(`Could not revoke the key (${resp.status})`);
  return resp.json();
}

export function getAdminFeedback(): Promise<AdminFeedback[]> {
  return adminGet<AdminFeedback[]>("/admin/feedback");
}

export function getEvalCases(): Promise<EvalCase[]> {
  return adminGet<EvalCase[]>("/admin/eval-cases");
}

// --- Golden Answer (Builder) — s14 E1 --------------------------------------
/** How a golden is scored (s24 M2 / grader-spec editor). Stored as the
 *  ``eval_cases.grader`` jsonb and consumed verbatim by the eval runner
 *  (`scripts/eval_run.py`) + `/agent/eval/grade`. The ``kind`` dispatches G1:
 *  ``scalar`` (one value, % tolerance), ``row_set`` (F1 over ``key``),
 *  ``ranked_set`` (top-``k`` overlap on ``key``), ``series`` (per-point
 *  tolerance on ``key``→``value``). ``key: "_key"`` + ``key_fields`` is a
 *  composite key the runner joins; ``aggregate: "ratio"`` rolls both sides to
 *  the key grain and rebuilds ``value`` = ``numerator``/``denominator`` (so a
 *  weighted average is graded, never an average-of-averages). ``expect_chart`` /
 *  ``min_slides`` are G5: what the delivered deck must contain. */
export interface GraderSpec {
  kind?: "scalar" | "row_set" | "ranked_set" | "series" | "";
  key?: string;
  key_fields?: string[];
  value?: string;
  k?: number;
  tolerance_pct?: number;
  aggregate?: "sum" | "ratio" | "";
  numerator?: string;
  denominator?: string;
  expect_chart?: boolean;
  min_slides?: number;
}

/** s49 M2 — golden v2. The judge grades the agent's answer against
 *  `golden_answer` (a human-written reference) and must return `label` for it;
 *  `calibration_examples` are extra answers with known labels that prove the
 *  judge still works. `checkpoints` are diagnostic per-stage expectations — they
 *  are never shown to the agent and never gate a case (decision D1). */
export type GoldenLabel = "low" | "medium" | "high";

export interface CalibrationExample {
  label: GoldenLabel;
  answer: string;
}

export interface GoldenCheckpoints {
  sql?: { key_cols?: string[] };
  analysis?: { expected_skills?: string[]; derived_cols?: string[] };
  deck?: { layouts_any_of?: string[]; kpi_label_contains?: string };
}

export interface GoldenListItem {
  id: string;
  dataset: string | null;
  tier: string | null;
  question: string;
  as_user: string | null;
  tags: string[];
  holdout: boolean;
  authoring_status: string;
  has_sql: boolean;
  has_sandbox: boolean;
  has_data: boolean;
  has_report: boolean;
  grader_kind: string | null;
  created_at: string;
  updated_at: string;
}

export interface GoldenFull extends GoldenListItem {
  source: string;
  expectation: string | null;
  golden_sql: string | null;
  golden_sandbox: string | null;
  golden_data: unknown;
  golden_report: unknown;
  grader?: GraderSpec | null;
  golden_answer?: string | null;
  label?: GoldenLabel | null;
  calibration_examples?: CalibrationExample[] | null;
  checkpoints?: GoldenCheckpoints | null;
}

export interface GoldenInput {
  question: string;
  dataset?: string | null;
  tier?: string | null;
  as_user?: string | null;
  tags?: string[];
  holdout?: boolean;
  authoring_status?: string;
  golden_sql?: string | null;
  golden_sandbox?: string | null;
  golden_data?: unknown;
  golden_report?: unknown;
  grader?: GraderSpec | null;
  expectation?: string | null;
  golden_answer?: string | null;
  label?: GoldenLabel | null;
  calibration_examples?: CalibrationExample[] | null;
  checkpoints?: GoldenCheckpoints | null;
}

/** The extract a golden's SQL runs to (respecting `as_user` RLS impersonation).
 *  The Goldens builder's Sandbox/report-object stages were removed with the
 *  report-engine (s46) — this is now purely "run the SQL, see the rows". */
export interface PrepResult {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  error: string | null;
}

async function adminSend<T>(path: string, method: string, body?: unknown): Promise<T> {
  const resp = await apiFetch(`${API}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`Admin request failed (${resp.status})`);
  return resp.json();
}

export function listGoldens(dataset?: string): Promise<GoldenListItem[]> {
  const q = dataset ? `?dataset=${encodeURIComponent(dataset)}` : "";
  return adminGet<GoldenListItem[]>(`/admin/eval-goldens${q}`);
}

export function getGolden(id: string): Promise<GoldenFull> {
  return adminGet<GoldenFull>(`/admin/eval-goldens/${id}`);
}

export function createGolden(body: GoldenInput): Promise<{ status: string; id: string }> {
  return adminPost<{ status: string; id: string }>("/admin/eval-goldens", body);
}

// Promote a stored chat answer into a draft golden (no agent re-run — the
// backend copies the run's captured SQL / sandbox script / report pages).
// Idempotent: re-promoting the same run returns the existing golden with
// created=false. Admin-only.
export function goldenFromRun(
  runId: string,
): Promise<{ status: string; id: string; created: boolean }> {
  return adminPost<{ status: string; id: string; created: boolean }>(
    "/admin/eval-goldens/from-run",
    { run_id: runId },
  );
}

export function updateGolden(
  id: string,
  patch: Partial<GoldenInput>,
): Promise<{ status: string; updated: number }> {
  return adminSend<{ status: string; updated: number }>(`/admin/eval-goldens/${id}`, "PUT", patch);
}

export function deleteGolden(id: string): Promise<{ status: string; deleted: number }> {
  return adminSend<{ status: string; deleted: number }>(`/admin/eval-goldens/${id}`, "DELETE");
}

export function prepGolden(body: { sql: string; as_user?: string | null }): Promise<PrepResult> {
  return adminPost<PrepResult>("/admin/eval-goldens/prep", body);
}

async function adminPost<T>(path: string, body: unknown): Promise<T> {
  const resp = await apiFetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`Admin request failed (${resp.status})`);
  return resp.json();
}

async function adminPut<T>(path: string, body: unknown): Promise<T> {
  const resp = await apiFetch(`${API}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`Admin request failed (${resp.status})`);
  return resp.json();
}

export function promoteFeedback(feedbackIds: string[]): Promise<{ created: number }> {
  return adminPost("/admin/feedback/promote", { feedback_ids: feedbackIds });
}

export function triageFeedback(id: string, action: "user_memory" | "dismiss"): Promise<unknown> {
  return adminPost(`/admin/feedback/${id}/triage`, { action });
}

export function setEvalCaseStatus(
  id: string,
  status: "active" | "stale" | "archived",
): Promise<unknown> {
  return adminPost(`/admin/eval-cases/${id}/status`, { status });
}

export function runEvalStaleness(): Promise<{
  checked: number;
  flagged_stale: number;
  archived: number;
}> {
  return adminPost("/admin/eval-cases/run-staleness", {});
}

export function track(eventType: string, payload: Record<string, unknown> = {}) {
  // Fire-and-forget product analytics. Every event carries the anonymous
  // visitor id (s38) so the Analytics tab can count uniques and returns.
  apiFetch(`${API}/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      event_type: eventType,
      session_id: sessionId,
      payload: { visitor_id: visitorId(), ...payload },
    }),
  }).catch(() => {});
}

/* ---------------------------------------------------------------------------
 * Evaluations (s24 M4) — read-only history of scored eval runs. Runs are
 * produced by `make eval`, never by the UI, so there is no write path here.
 * ------------------------------------------------------------------------- */

export interface EvalAgentVersion {
  fingerprint: string | null;
  label: string | null;
  provider: string | null;
  model_id: string | null;
  prompt_hash: string | null;
  skills_hash: string | null;
  knowledge_version: string | null;
}

export interface EvalRun {
  id: string;
  started_at: string | null;
  finished_at: string | null;
  dataset: string;
  pack: string;
  pack_version: string;
  experiment_id: string | null;
  hypothesis: string | null;
  base_run_id: string | null;
  judge_model: string | null;
  judge_prompt_hash: string | null;
  totals: {
    cases?: number;
    passed?: number;
    errors?: number;
    pass_rate?: number;
    g1_mean?: number | null;
    g4_turns_mean?: number | null;
    generalisation?: string;
    /** s49 M2: the judge's verdicts as a distribution — three ordered labels
     *  have no meaningful mean, so none is reported. */
    judge_labels?: { high?: number; medium?: number; low?: number };
    /** Whether the judge's label participated in `passed` for this run's pack
     *  size (false below HOLDOUT_MIN_CASES goldens — decision D2). */
    judge_gates?: boolean;
    judge_calibration?: {
      calibrated?: boolean;
      probes?: number;
      agreed?: number;
      model?: string | null;
      rubric_hash?: string | null;
    };
  };
  agent: EvalAgentVersion;
}

export interface EvalCaseResult {
  case_key: string;
  question: string;
  dataset: string;
  tier: string | null;
  holdout: boolean;
  passed: boolean | null;
  notes: string | null;
  query_run_id: string | null;
  g1: { kind?: string; score?: number | null; error?: string };
  g2: { score?: number; expected_objects?: string[]; built_object_types?: string[] };
  g3: {
    format?: { passed?: boolean; issues?: string[]; object_types?: string[] };
  };
  g4: { turns?: number; latency_ms?: number; input_tokens?: number | null };
  /** s49 M2 — the judge's verdict. Recorded and displayed; it does not gate. */
  judge?: {
    label?: GoldenLabel | null;
    diagnosis?: "sql" | "analysis" | "presentation" | "knowledge" | "none" | null;
    reason?: string;
    model?: string;
    effort?: string;
    calibrated?: boolean;
    skipped?: boolean;
    rubric_hash?: string;
  };
  /** Diagnostic per-stage scores (0-1, or null when unspecified). Never gates. */
  checkpoints?: {
    sql?: { score?: number | null; rows_match?: number; missing?: string[] };
    analysis?: { score?: number | null; missing_skills?: string[]; missing_cols?: string[] };
    deck?: { score?: number | null; layout_hit?: boolean; kpi_hit?: boolean };
  };
}

export interface EvalComparison {
  base: EvalRun | null;
  comparable: boolean;
  regressed: string[];
  fixed: string[];
  gate: "PASS" | "FAIL";
}

export interface EvalRunDetail {
  run: EvalRun;
  results: EvalCaseResult[];
  comparison: EvalComparison | null;
}

export function getEvalRuns(limit = 50): Promise<EvalRun[]> {
  return adminGet<EvalRun[]>(`/admin/eval-runs?limit=${limit}`);
}

export function getEvalRun(runId: string): Promise<EvalRunDetail> {
  return adminGet<EvalRunDetail>(`/admin/eval-runs/${runId}`);
}

/* ---------------------------------------------------------------------------
 * Ops flight deck (s32) — one pre-aggregated read per window (decision Q3).
 * The shapes mirror services/backend-api/app/ops_rollup.py; every field is
 * optional because a panel whose workstream hasn't produced rows yet must read
 * "no data", never break.
 * ------------------------------------------------------------------------- */

export type OpsWindow = "24h" | "7d" | "28d";
export type LampStateName = "off" | "on" | "warn" | "bad";

export interface OpsLatency {
  asks?: number;
  answer_p50_ms?: number | null;
  answer_p95_ms?: number | null;
  answer_p99_ms?: number | null;
  ttfp_p50_ms?: number | null;
  ttfp_p95_ms?: number | null;
  ttfp_p99_ms?: number | null;
}

export interface OpsErrors {
  runs?: number;
  errors?: number;
  degraded?: number;
  no_answer?: number;
  error_rate?: number | null;
  degraded_rate?: number | null;
  no_answer_rate?: number | null;
  by_source?: Record<string, number>;
}

export interface OpsTraffic {
  runs?: number;
  asks?: number;
  active_users?: number;
  asks_per_user?: number | null;
  by_source?: Record<string, number>;
}

export interface OpsCost {
  total_usd?: number | null;
  priced_asks?: number;
  per_answer_usd?: number | null;
  input_tokens?: number;
  output_tokens?: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  cache_hit_ratio?: number | null;
  budget_usd?: number | null;
}

export interface OpsSecurityRun {
  created_at: string | null;
  kind: string;
  total: number;
  passed: number;
  pass_rate: number | null;
  by_category: Record<string, unknown>;
  report_url: string | null;
}

export interface OpsSecurity {
  denials?: number;
  auth_failures?: number;
  cap_hits?: number;
  latest_run?: OpsSecurityRun | null;
}

export interface OpsLoadTest {
  created_at: string | null;
  scenario: string | null;
  vus: number | null;
  rps: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  error_rate: number | null;
}

export interface OpsReliability {
  attempts_mean?: number | null;
  retried?: number;
  db_cold_starts?: number;
  latest_load_test?: OpsLoadTest | null;
}

export interface OpsSlo {
  window?: string;
  availability?: {
    target?: number;
    attained?: number | null;
    asks?: number;
    served?: number;
    error_budget_burn?: number | null;
    state?: LampStateName;
  };
  responsiveness?: {
    target_ms?: number;
    attained_ms?: number | null;
    measured?: number;
    fast?: number;
    state?: LampStateName;
  };
}

export interface OpsFreshness {
  available?: boolean;
  created_at?: string | null;
  status?: string;
  duration_s?: number | null;
  age_s?: number | null;
  dbt_pass?: number | null;
  dbt_total?: number | null;
  row_counts?: Record<string, unknown>;
  source?: string | null;
  state?: LampStateName;
}

export interface OpsDeploy {
  id: string;
  started_at: string | null;
  finished_at: string | null;
  git_sha: string;
  actor: string | null;
  status: string;
  duration_s: number | null;
  smoke: Record<string, unknown>;
}

export interface OpsSaturation {
  available?: boolean;
  reason?: string;
  fetched_at?: string;
  backend?: { cpu_pct?: number | null; mem_pct?: number | null; instances?: number | null };
  agent?: { cpu_pct?: number | null; mem_pct?: number | null; instances?: number | null };
  aurora?: { acu?: number | null; connections?: number | null };
  cdn?: { cache_hit_rate?: number | null };
  limits?: { max_concurrency?: number | null };
}

export interface OpsMetrics {
  window?: string;
  interval?: string;
  latency?: OpsLatency;
  errors?: OpsErrors;
  traffic?: OpsTraffic;
  cost?: OpsCost;
  product?: { thumbs_up?: number; thumbs_down?: number; thumbs_up_rate?: number | null };
  security?: OpsSecurity;
  reliability?: OpsReliability;
  judge?: { sampled?: number; insight_mean?: number | null; latest_at?: string | null };
  slo?: OpsSlo;
  freshness?: OpsFreshness;
  deploys?: OpsDeploy[];
  saturation?: OpsSaturation;
}

export interface OpsSummary {
  window: OpsWindow;
  windows: OpsWindow[];
  refreshed_at: string | null;
  age_s: number | null;
  stale: boolean;
  metrics: OpsMetrics;
}

export interface OpsRun {
  id: string;
  created_at: string | null;
  latency_ms: number | null;
  ttfp_ms: number | null;
  status: string;
  degraded: boolean;
  attempts: number | null;
  cost_usd: number | null;
  otel_trace_id: string | null;
  engine: string;
  question: string;
  username: string;
}

export function getOpsSummary(window: OpsWindow = "24h"): Promise<OpsSummary> {
  return adminGet<OpsSummary>(`/admin/ops/summary?window=${window}`);
}

export function getOpsRuns(limit = 25): Promise<OpsRun[]> {
  return adminGet<OpsRun[]>(`/admin/ops/runs?limit=${limit}`);
}

export function refreshOps(): Promise<{ refreshed: string[] }> {
  return adminPost<{ refreshed: string[] }>("/admin/ops/refresh", {});
}

/* ---------------------------------------------------------------------------
 * Architecture tab (M5, agent_sdk migration) — a live snapshot of the GenAI
 * system itself, proxied from the data-agent's GET /agent/architecture(/content).
 * The run walk-through panel reuses getAdminQueryRuns above (source: "agent"),
 * not a dedicated endpoint — see services/backend-api/app/routers/architecture.py.
 * ------------------------------------------------------------------------- */

export interface ArchitectureRuntime {
  agent_runtime: string; // "pydantic_ai" (champion) | "agent_sdk" (challenger)
  model: string;
  provider: string;
  sandbox_runtime: string;
  quotas: Record<string, number>;
  fingerprint: Record<string, string>; // av-* + component hashes (version.build_fingerprint)
}

export interface ArchitectureKnowledgeFile {
  kind: "claude_md" | "marts" | "schema" | "knowledge";
  id: string; // pass as `name` to getArchitectureContent; "" when not needed
  filename: string; // the name this file has inside a real run workspace
  label: string;
  description: string;
  size: number;
  sha256?: string | null;
  // s49 (D3): only meaningful for kind === "knowledge" — "file" (default) or
  // "db" (a curator override not yet exported), with its DB row version.
  source?: "file" | "db";
  version?: number;
}

export interface ArchitectureTool {
  kind: "mcp" | "builtin";
  name: string;
  server: string | null;
  description: string;
  input_schema: Record<string, unknown> | null;
  quota: string;
  guardrail: string;
}

export interface ArchitectureData {
  available: boolean;
  error?: string;
  runtime?: ArchitectureRuntime;
  knowledge?: { knowledge_version: string; files: ArchitectureKnowledgeFile[] };
  tools?: ArchitectureTool[];
}

export function getArchitecture(): Promise<ArchitectureData> {
  return adminGet<ArchitectureData>("/architecture");
}

export function getArchitectureContent(kind: string, name = ""): Promise<{ content: string }> {
  const qs = new URLSearchParams({ kind, name });
  return adminGet<{ content: string }>(`/architecture/content?${qs}`);
}

/* ---------------------------------------------------------------------------
 * Pack Inspector (s48 §P2) — the synced template pack's admin tab: what the
 * agent's slide/chart menu actually is right now, straight from Google via
 * the data-agent's GET/PUT /agent/pack(/layouts/{id}), proxied at
 * /admin/pack (services/backend-api/app/routers/admin_pack.py).
 * ------------------------------------------------------------------------- */

export interface PackLayout {
  id: string;
  name: string;
  enabled: boolean;
  use_when: string;
  source: string;
  slots: string[];
  table_template: string;
  chart_template: string | null; // "<tab>!<chart title>"
  grader_shape: string;
  issues: string[];
  thumbnail_url: string | null;
}

export interface Pack {
  name: string;
  version: number;
  synced_at: string;
  slides_url: string;
  sheet_url: string;
  folder_url: string;
  layouts: PackLayout[];
  issues: string[];
  stale: boolean;
}

export interface PackLayoutUpdate {
  enabled?: boolean;
  use_when?: string;
}

export function getPack(): Promise<Pack> {
  return adminGet<Pack>("/admin/pack");
}

export function updatePackLayout(layoutId: string, update: PackLayoutUpdate): Promise<Pack> {
  return adminPut<Pack>(`/admin/pack/layouts/${layoutId}`, update);
}

/* ---------------------------------------------------------------------------
 * Knowledge curator (s49 M4, D3) — read/edit one knowledge page from the
 * Architecture tab's edit box. Reads proxy the data-agent's GET
 * /agent/knowledge(/{path}) (it holds the markdown files); the PUT writes
 * app.knowledge_pages directly in backend-api (services/backend-api/app/
 * routers/admin_knowledge.py) — the data-agent has no DB role that can write
 * it, see that router's module docstring.
 * ------------------------------------------------------------------------- */

export interface KnowledgePageMeta {
  path: string;
  name: string;
  description: string;
  source: "file" | "db";
  version: number;
  author: string;
  updated_at: string;
}

export interface KnowledgePage extends KnowledgePageMeta {
  body: string;
}

export function listKnowledgePages(): Promise<KnowledgePageMeta[]> {
  return adminGet<KnowledgePageMeta[]>("/admin/knowledge");
}

export function getKnowledgePage(path: string): Promise<KnowledgePage> {
  return adminGet<KnowledgePage>(`/admin/knowledge/${encodeURIComponent(path)}`);
}

export function saveKnowledgePage(
  path: string,
  body: string,
  author = "",
): Promise<KnowledgePage> {
  return adminPut<KnowledgePage>(`/admin/knowledge/${encodeURIComponent(path)}`, { body, author });
}
