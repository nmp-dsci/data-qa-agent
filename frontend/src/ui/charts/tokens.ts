// Chart tokens — every visx chart reads its colors from the design tokens in
// styles.css, so charts follow the active theme (dark/light) automatically.

export function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** The categorical palette (--chart-1..5), in series order. */
export function chartPalette(): string[] {
  return [
    cssVar("--chart-1", "#d9a84e"),
    cssVar("--chart-2", "#7fb0ff"),
    cssVar("--chart-3", "#9ece6a"),
    cssVar("--chart-4", "#c58fff"),
    cssVar("--chart-5", "#7dcfff"),
  ];
}

export function chartTheme() {
  return {
    grid: cssVar("--chart-grid", "#1d2434"),
    axis: cssVar("--chart-axis", "#242b3d"),
    label: cssVar("--chart-label", "#9aa4bb"),
    text: cssVar("--text", "#eaecf3"),
    good: cssVar("--good", "#9ece6a"),
    bad: cssVar("--bad", "#f2777a"),
  };
}

/**
 * Coerce whatever a stored report carries in a chart's `rows` field into a real
 * array the renderers can iterate. A report can arrive with `rows` as a plain
 * array (the normal case), as the eval-pack exporter's `{_truncated,_head,…}`
 * digest (a chart's rows capped for the version-controlled pack), or as
 * `undefined`/garbage from a malformed object. A chart must never throw on
 * `for…of` — an unusable shape renders empty, not a white screen.
 */
export function asRows(v: unknown): Record<string, unknown>[] {
  if (Array.isArray(v)) return v as Record<string, unknown>[];
  if (v && typeof v === "object" && Array.isArray((v as { _head?: unknown })._head)) {
    return (v as { _head: Record<string, unknown>[] })._head;
  }
  return [];
}

export function formatValue(v: number, field: string): string {
  const currency = /price|value|rent|cost|amount|\$/i.test(field);
  const abs = Math.abs(v);
  const compact =
    abs >= 1_000_000
      ? `${(v / 1_000_000).toFixed(1)}M`
      : abs >= 10_000
        ? `${Math.round(v / 1000)}k`
        : abs >= 1000
          ? v.toLocaleString(undefined, { maximumFractionDigits: 0 })
          : `${Math.round(v * 100) / 100}`;
  return currency ? `$${compact}` : compact;
}
