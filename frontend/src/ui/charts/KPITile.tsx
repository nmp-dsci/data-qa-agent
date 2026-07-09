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
  growth?: { yoy?: number | null; mom?: number | null } | null;
  series?: Record<string, unknown>[] | null;
}

function fmtGrowth(g: number): string {
  const pct = Math.abs(g) <= 1 ? g * 100 : g; // accept fraction or percent
  return `${pct >= 0 ? "▲ +" : "▼ "}${pct.toFixed(1)}%`;
}

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
  const growth = data.growth?.yoy ?? data.growth?.mom ?? null;
  const growthLabel = data.growth?.yoy != null ? "YoY" : data.growth?.mom != null ? "MoM" : "";
  return (
    <div className="kpi-tile">
      <div className="h-label">{data.label}</div>
      <div className="h-value">
        {display}
        {growth != null && (
          <span className={`kpi-growth ${growth >= 0 ? "up" : "down"}`}>
            {fmtGrowth(growth)} {growthLabel}
          </span>
        )}
      </div>
      {data.basis && <div className="h-basis">{data.basis}</div>}
      {data.series && data.series.length > 1 && <Sparkline series={data.series} />}
    </div>
  );
}
