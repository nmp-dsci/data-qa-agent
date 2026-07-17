// KPITile — latest number + secondary growth rate (+ optional sparkline).
// The Summary page's hero object.
import { useMemo } from "react";
import { chartPalette } from "./tokens";

export interface KPIData {
  label: string;
  value?: string | number | null;
  latest?: number | null;
  unit?: string | null;
  basis?: string | null;
  /** Cohort colour for the label — the Explore profile's Target gold /
   *  Comparison blue identity (same tokens DataTable's column tones use). */
  tone?: "target" | "comparison" | null;
  /** `pct` is ALWAYS a percent (no fraction heuristic) with an optional custom
   *  `label` (e.g. "vs comparison"); yoy/mom keep the legacy heuristic. */
  growth?: {
    yoy?: number | null;
    mom?: number | null;
    pct?: number | null;
    label?: string | null;
  } | null;
  series?: Record<string, unknown>[] | null;
}

function fmtGrowth(g: number, alreadyPct = false): string {
  const pct = alreadyPct || Math.abs(g) > 1 ? g : g * 100; // accept fraction or percent
  return `${pct >= 0 ? "▲ +" : "▼ "}${pct.toFixed(1)}%`;
}

const TONE_STYLE: Record<string, React.CSSProperties> = {
  target: { color: "var(--cohort-target, #d9a84e)" },
  comparison: { color: "var(--cohort-comparison, #7dcfff)" },
};

function Sparkline({ series }: { series: Record<string, unknown>[] }) {
  const [stroke] = chartPalette();
  const points = useMemo(() => {
    const vals = series
      .map((r) => Number(r["value"]))
      .filter((v) => Number.isFinite(v))
      .slice(-24);
    if (vals.length < 2) return "";
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const span = hi - lo || 1;
    const w = 96;
    const h = 26;
    return vals
      .map((v, i) => `${((i / (vals.length - 1)) * w).toFixed(1)},${(h - ((v - lo) / span) * h).toFixed(1)}`)
      .join(" ");
  }, [series]);
  if (!points) return null;
  return (
    <svg width={96} height={28} className="kpi-spark" aria-hidden="true">
      <polyline points={points} fill="none" stroke={stroke} strokeWidth={1.5} opacity={0.8} />
    </svg>
  );
}

export function KPITile({ data }: { data: KPIData }) {
  const display =
    data.value != null && data.value !== ""
      ? String(data.value)
      : data.latest != null
        ? `${data.latest.toLocaleString()}${data.unit ? ` ${data.unit}` : ""}`
        : "n/a";
  const growth = data.growth?.yoy ?? data.growth?.mom ?? data.growth?.pct ?? null;
  const growthLabel =
    data.growth?.label ??
    (data.growth?.yoy != null ? "YoY" : data.growth?.mom != null ? "MoM" : "");
  const alreadyPct = data.growth?.yoy == null && data.growth?.mom == null;
  return (
    <div className="kpi-tile">
      <div className="h-label" style={data.tone ? TONE_STYLE[data.tone] : undefined}>
        {data.label}
      </div>
      <div className="h-value">
        {display}
        {growth != null && (
          <span className={`kpi-growth ${growth >= 0 ? "up" : "down"}`}>
            {fmtGrowth(growth, alreadyPct)} {growthLabel}
          </span>
        )}
      </div>
      {data.basis && <div className="h-basis">{data.basis}</div>}
      {data.series && data.series.length > 1 && <Sparkline series={data.series} />}
    </div>
  );
}
