export function formatTime(value: string): string {
  return new Date(value).toLocaleString();
}

/** Postcode dimensions carry an ABS Postal Area code. Bare, "2077" reads as any
 *  four-digit number, so label it wherever a user picks or reads one — the map
 *  tooltip and the value pickers. Display only: the stored value, the filter
 *  sent to the API and the SQL predicate all stay the bare code. */
export function formatPoa(value: string | number): string {
  const s = String(value);
  return s.startsWith("POA:") ? s : `POA:${s}`;
}

/** Strip a typed "POA:" prefix so searching "POA:20" finds postcode 2077. */
export function stripPoa(query: string): string {
  return query.replace(/^\s*poa\s*:?\s*/i, "");
}

/** Whether a dimension's values are postcodes (and so read better prefixed). */
export function isPoaDimension(name: string): boolean {
  return name === "postcode";
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
