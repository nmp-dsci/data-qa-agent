const API = (import.meta.env.VITE_API_URL as string) ?? "http://localhost:8000";

export interface User {
  id: string;
  username: string;
  email: string;
  display_name: string;
  role: string;
}

export interface AuthConfig {
  auth_mode: "dev" | "google";
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

// Pages contract (s08 column model): the agent's answer as an ordered list of
// pages, each naming a frontend-owned template and carrying ordered columns of
// typed objects with data + intent (never chart specs or CSS). Placement is
// positional (columns[i][j]); `role` is the semantic label (headline / chart /
// insight — feedback + evals key off it) and never affects placement. Objects
// may carry data.height (px or "sm"|"md"|"lg"|"fill").
export type PageObjectType = "kpi" | "trend" | "breakdown" | "compare" | "insight" | "text";

export interface PageObject {
  type: PageObjectType;
  element_id: string;
  role?: string | null;
  data: Record<string, unknown>;
  explains?: string | null;
}

export type TemplateId = "one-col" | "two-col" | "three-col";

export interface Page {
  template: TemplateId;
  columns: PageObject[][];
  /** Optional page-level headline that summarises what the page shows. */
  headline?: string | null;
  /** Optional per-column relative widths (fr weights) overriding the template's
   *  default tracks; one entry per column, left→right. */
  widths?: number[] | null;
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
  steps: AgentStep[];
  report: InsightReport | null;
  pages: Page[] | null;
}

export interface FeedbackInput {
  message_id: string;
  rating: 1 | -1;
  accurate: boolean | null;
  issue_flag: boolean;
  comment?: string;
  target_kind: string;
  target_ref: string;
  target_snapshot: Record<string, unknown>;
  target_render_html: string;
  report_snapshot: InsightReport;
  knowledge_version: string;
  knowledge_pages: string[];
  client_context: Record<string, unknown>;
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

export function setToken(t: string | null) {
  token = t;
}

// Marks every request as coming from the web app so query runs are attributed
// to the 'web' channel in app.query_runs (a direct API hit has no such header
// and is recorded as 'api'). Sent on all requests; the backend only reads it
// where it audits a run (/ask, /sql).
function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = { "X-Client-Channel": "web" };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

export async function getAuthConfig(): Promise<AuthConfig> {
  const resp = await fetch(`${API}/auth/config`);
  if (!resp.ok) throw new Error(`Could not load auth config (${resp.status})`);
  return resp.json();
}

export async function devLogin(username: string): Promise<{ access_token: string; user: User }> {
  const resp = await fetch(`${API}/auth/dev-login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username }),
  });
  if (!resp.ok) throw new Error(`Login failed (${resp.status})`);
  return resp.json();
}

export async function getMe(): Promise<User> {
  const resp = await fetch(`${API}/me`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`Could not load profile (${resp.status})`);
  return resp.json();
}

export async function ask(
  question: string,
  conversationId: string | null,
  signal?: AbortSignal,
): Promise<AskResult> {
  const resp = await fetch(`${API}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ question, conversation_id: conversationId }),
    signal,
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`Ask failed (${resp.status}): ${detail}`);
  }
  return resp.json();
}

export interface AskStatus {
  state: string;
  elapsed_s?: number;
}

/** A live agent step while the answer is being built (running step list). */
export interface AskProgress {
  n: number;
  action: string;
  detail?: string;
}

// s10 streaming pages: the `plan` frame declares up front how many pages this
// answer will complete for this user (locked = paywall teaser for pages above
// their plan); one `page` frame then arrives per finished page carrying the
// exact Template Studio Page JSON. `result` stays authoritative — the UI
// reconciles streamed pages against result.pages when it lands.
export type PageSlotStatus = "planned" | "building" | "complete" | "skipped" | "locked";

export interface PagePlanSlot {
  index: number;
  kind: string; // summary | insights | opportunities
  template?: TemplateId;
  status: PageSlotStatus;
}

export interface PageFrame {
  index: number;
  kind?: string;
  status: string; // complete | skipped
  page?: Page;
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
  let resp: Response;
  try {
    resp = await fetch(`${API}/ask/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ question, conversation_id: conversationId }),
      signal,
    });
  } catch (e) {
    // A user-initiated stop must not fall back to the blocking ask().
    if (signal?.aborted) throw e;
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
  const resp = await fetch(`${API}/sql`, {
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
  const resp = await fetch(`${API}/sql/history?limit=${limit}`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`Could not load history (${resp.status})`);
  return resp.json();
}

export async function runSqlAi(
  action: AiAction,
  args: { prompt?: string; sql?: string },
): Promise<AiAssistResult> {
  const resp = await fetch(`${API}/sql/ai`, {
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
  const resp = await fetch(`${API}/schema/catalog`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`Could not load schema (${resp.status})`);
  const data = (await resp.json()) as { tables: CatalogTable[] };
  return data.tables ?? [];
}

async function adminGet<T>(path: string): Promise<T> {
  const resp = await fetch(`${API}${path}`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`Admin request failed (${resp.status})`);
  return resp.json();
}

export function getAdminEvents(): Promise<AdminEvent[]> {
  return adminGet<AdminEvent[]>("/admin/events");
}

export function getAdminUsers(): Promise<AdminUser[]> {
  return adminGet<AdminUser[]>("/admin/users");
}

export function getAdminDatasets(): Promise<AdminDataset[]> {
  return adminGet<AdminDataset[]>("/admin/datasets");
}

export function getAdminQueryRuns(): Promise<AdminQueryRun[]> {
  return adminGet<AdminQueryRun[]>("/admin/query-runs");
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
  report: (InsightReport & { pages?: Page[] }) | null;
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
  const resp = await fetch(`${API}/conversations`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`Could not load conversations (${resp.status})`);
  return resp.json();
}

export async function getConversationMessages(id: string): Promise<ConversationMessage[]> {
  const resp = await fetch(`${API}/conversations/${id}/messages`, { headers: authHeaders() });
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
  const resp = await fetch(`${API}/me/memories`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`Could not load memories (${resp.status})`);
  return resp.json();
}

export async function deleteMyMemory(id: string): Promise<{ deleted: boolean }> {
  const resp = await fetch(`${API}/me/memories/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!resp.ok) throw new Error(`Could not delete memory (${resp.status})`);
  return resp.json();
}

export async function getMyAccess(): Promise<MyAccess> {
  const resp = await fetch(`${API}/me/access`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`Could not load access (${resp.status})`);
  return resp.json();
}

export async function submitFeedback(input: FeedbackInput): Promise<{ id: string }> {
  const resp = await fetch(`${API}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(input),
  });
  if (!resp.ok) throw new Error(`Feedback failed (${resp.status})`);
  return resp.json();
}

export function getAdminFeedback(): Promise<AdminFeedback[]> {
  return adminGet<AdminFeedback[]>("/admin/feedback");
}

export function getEvalCases(): Promise<EvalCase[]> {
  return adminGet<EvalCase[]>("/admin/eval-cases");
}

// --- Golden Answer (Builder) — s14 E1 --------------------------------------
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
  expectation?: string | null;
}

/** A derived frame the sandbox built and fed to a skill — the enrichment stage
 *  between the SQL extract and the report objects (Golden builder Sandbox view). */
export interface SandboxFrame {
  name: string;
  columns: string[];
  rows: unknown[][];
  shape: [number, number];
  /** True when this frame was fed to a skill, so its data is in a chart/analysis
   *  object; false when it's a derived frame behind a KPI/scalar. */
  fed_object?: boolean;
}

export interface PrepResult {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  report: Record<string, unknown> | null;
  pages?: Page[] | null;
  frames?: SandboxFrame[];
  skills_used: string[];
  skill_gaps: { need: string; why: string }[];
  error: string | null;
}

async function adminSend<T>(path: string, method: string, body?: unknown): Promise<T> {
  const resp = await fetch(`${API}${path}`, {
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

export interface SkillInfo {
  name: string;
  group: string;
  doc: string;
  signature: string;
}

export function getGoldenSkills(): Promise<{ skills: SkillInfo[] }> {
  return adminGet<{ skills: SkillInfo[] }>("/admin/eval-goldens/skills");
}

export interface ScaffoldResult {
  code: string;
  reasoning: { skill: string; why: string }[];
  engine: string;
  error: string | null;
}

export function scaffoldGolden(body: {
  question: string;
  columns: string[];
  skills: string[];
}): Promise<ScaffoldResult> {
  return adminPost<ScaffoldResult>("/admin/eval-goldens/scaffold", body);
}

export function createGolden(body: GoldenInput): Promise<{ status: string; id: string }> {
  return adminPost<{ status: string; id: string }>("/admin/eval-goldens", body);
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

export function prepGolden(body: {
  sql: string;
  code?: string;
  as_user?: string | null;
}): Promise<PrepResult> {
  return adminPost<PrepResult>("/admin/eval-goldens/prep", body);
}

// Author one report object from a plain-English instruction: the agent rewrites
// run_analysis to build the described chart, runs it in the sandbox, and returns
// the lifted object (type + data) plus the refreshed sandbox report + code.
export interface AuthorObjectResult {
  code: string;
  // The extract that produced this — the revised SQL when the agent had to add
  // columns for the requested data, else the caller's SQL unchanged (s16).
  sql: string | null;
  object: { type: PageObjectType; data: Record<string, unknown> } | null;
  report: Record<string, unknown> | null;
  // The FULL recomposed report as pages (every object with fresh data) so the
  // builder can refresh the whole presentation in sync, not just one object.
  pages: Page[] | null;
  columns: string[];
  rows: unknown[][];
  reasoning: { skill: string; why: string }[];
  engine: string;
  skills_used: string[];
  skill_gaps: { need: string; why: string }[];
  error: string | null;
}

/** A slim digest of a presentation object (no row payload) — tells the agent what
 *  to preserve and marks which object is being edited. */
export interface ObjectDigest {
  element_id: string;
  type: string;
  role: string | null;
  data: Record<string, unknown>;
  _target?: boolean;
}

export function authorObject(body: {
  sql: string;
  code?: string;
  object_type: string;
  instruction: string;
  objects?: ObjectDigest[];
  target_element_id?: string | null;
  as_user?: string | null;
}): Promise<AuthorObjectResult> {
  return adminPost<AuthorObjectResult>("/admin/eval-goldens/object", body);
}

export interface DraftResult {
  sql: string | null;
  sandbox: string;
  columns: string[];
  rows: unknown[][];
  report: Record<string, unknown> | null;
  pages: Page[] | null;
  summary: string | null;
}

export function draftGolden(body: {
  question: string;
  as_user?: string | null;
  dataset?: string;
}): Promise<DraftResult> {
  return adminPost<DraftResult>("/admin/eval-goldens/draft", body);
}

/** Streaming draft: onStatus fires once per streamed agent object (a single
 *  updating line); resolves with the final shaped golden. */
export async function draftGoldenStream(
  body: { question: string; as_user?: string | null; dataset?: string },
  onStatus: (label: string) => void,
): Promise<DraftResult> {
  const resp = await fetch(`${API}/admin/eval-goldens/draft/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!resp.ok || !resp.body) throw new Error(`Draft failed (${resp.status})`);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let draft: DraftResult | null = null;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    for (;;) {
      const sep = buffer.indexOf("\n\n");
      if (sep === -1) break;
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const eventLine = frame.split("\n").find((l) => l.startsWith("event: "));
      const dataLine = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!eventLine || !dataLine) continue;
      const event = eventLine.slice(7).trim();
      const raw = dataLine.slice(6);
      if (event === "status") {
        try {
          onStatus(String((JSON.parse(raw) as { label?: string }).label ?? ""));
        } catch {
          /* ignore malformed status */
        }
      } else if (event === "draft") {
        draft = JSON.parse(raw) as DraftResult;
      } else if (event === "error") {
        let detail = "draft error";
        try {
          detail = String((JSON.parse(raw) as { detail?: string }).detail ?? detail);
        } catch {
          /* ignore */
        }
        throw new Error(detail);
      }
    }
  }
  if (!draft) throw new Error("Draft stream ended without a result");
  return draft;
}

async function adminPost<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(`${API}${path}`, {
    method: "POST",
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
  // Fire-and-forget product analytics.
  fetch(`${API}/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ event_type: eventType, session_id: sessionId, payload }),
  }).catch(() => {});
}
