const API = (import.meta.env.VITE_API_URL as string) ?? "http://localhost:8000";

export interface User {
  id: string;
  username: string;
  email: string;
  display_name: string;
  role: string;
}

export interface AuthConfig {
  auth_mode: "dev" | "entra";
  authority?: string | null;
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

// Pages contract (s07): the agent's answer as an ordered list of pages, each
// naming a frontend-owned template and filling its regions with typed objects
// carrying data + intent (never chart specs or layout).
export type PageObjectType = "kpi" | "trend" | "breakdown" | "compare" | "insight" | "text";

export interface PageObject {
  type: PageObjectType;
  element_id: string;
  region: string;
  data: Record<string, unknown>;
  explains?: string | null;
}

export interface Page {
  template: "summary" | "insights" | "one-col" | "two-col";
  objects: PageObject[];
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

export async function ask(question: string, conversationId: string | null): Promise<AskResult> {
  const resp = await fetch(`${API}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ question, conversation_id: conversationId }),
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`Ask failed (${resp.status}): ${detail}`);
  }
  return resp.json();
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
