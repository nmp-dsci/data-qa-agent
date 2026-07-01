const API = (import.meta.env.VITE_API_URL as string) ?? "http://localhost:8000";

export interface User {
  id: string;
  username: string;
  email: string;
  display_name: string;
  role: string;
}

export interface AskResult {
  conversation_id: string;
  message_id: string;
  answer: string;
  sql: string | null;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  engine: string;
}

export interface AdminEvent {
  id: string;
  event_type: string;
  created_at: string;
  payload: Record<string, unknown>;
  username: string | null;
}

export interface AdminUser {
  id: string;
  username: string;
  email: string;
  display_name: string;
  role: string;
}

export interface AdminDataset {
  id: string;
  slug: string;
  name: string;
  status: string;
  row_count: number;
}

export interface AdminQueryRun {
  id: string;
  created_at: string;
  username: string;
  dataset: string | null;
  engine: string;
  row_count: number;
  latency_ms: number | null;
  status: string;
  question: string;
  sql_text: string | null;
  error: string | null;
}

let token: string | null = null;
let sessionId = Math.random().toString(36).slice(2);

export function setToken(t: string | null) {
  token = t;
}

function authHeaders(): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
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

export function track(eventType: string, payload: Record<string, unknown> = {}) {
  // Fire-and-forget product analytics.
  fetch(`${API}/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ event_type: eventType, session_id: sessionId, payload }),
  }).catch(() => {});
}
