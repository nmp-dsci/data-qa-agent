export function formatTime(value: string): string {
  return new Date(value).toLocaleString();
}

export function fmtTokens(n?: number | null): string {
  return n == null ? "—" : n.toLocaleString();
}

export function summarizeSnapshot(snap: Record<string, unknown>): string {
  if (!snap) return "";
  if (typeof snap.heading === "string") return snap.heading;
  if (typeof snap.label === "string") return `${snap.label}: ${snap.value ?? ""}`;
  const s = JSON.stringify(snap);
  return s.length > 120 ? s.slice(0, 120) + "…" : s;
}
