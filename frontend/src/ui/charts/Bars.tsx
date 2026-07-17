// Bars — breakdown (metric by one dimension) and compare (grouped by a second
// series). Powers the Insights driver view, comparison answers, and the SQL
// editor's result→chart. Colors + labels come from the design tokens.
import { useMemo, useRef, useState } from "react";
import { AxisBottom, AxisLeft } from "@visx/axis";
import { Group } from "@visx/group";
import { ParentSize } from "@visx/responsive";
import { scaleBand, scaleLinear } from "@visx/scale";
import { Bar } from "@visx/shape";
import { ChartTip, TipState } from "./ChartTip";
import { downloadSvgAsPng } from "./exportPng";
import { ChartSqlButton } from "./sqlLink";
import { chartPalette, chartTheme, cssVar, formatValue } from "./tokens";

export interface BarsData {
  dimension: string;
  measure: string;
  group?: string | null;
  title?: string | null;
  /** Stack the groups within each category instead of clustering them side by
   *  side (the legacy Trends "stacked bar" view). Requires a `group`. */
  stacked?: boolean;
  /** Fix the series order (and therefore palette colour) instead of sorting
   *  alphabetically — e.g. ["Target","Comparison"] to pin Target=gold, Comp=blue. */
  groupOrder?: string[];
  /** Sort the x categories ascending (date/number-aware) — for time-series
   *  stacked bars where the x axis must read left-to-right in order. */
  sortX?: boolean;
  /** The query behind this chart — enables the "open in SQL editor" action. */
  sql?: string | null;
  rows: Record<string, unknown>[];
}

/** Ascending compare that understands numbers and dates, else falls back to text. */
function catCompare(a: string, b: string): number {
  const na = Number(a);
  const nb = Number(b);
  if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
  const da = Date.parse(a);
  const db = Date.parse(b);
  if (!Number.isNaN(da) && !Number.isNaN(db)) return da - db;
  return a.localeCompare(b);
}

interface Datum {
  category: string;
  value: number;
  group: string;
  growth: number | null;
}

function parseData(data: BarsData): Datum[] {
  const out: Datum[] = [];
  for (const row of data.rows) {
    const value = Number(row[data.measure]);
    const category = row[data.dimension];
    if (category == null || !Number.isFinite(value)) continue;
    const growthRaw = row["growth"] ?? row["growth_pct"] ?? row["delta_pct"];
    out.push({
      category: String(category),
      value,
      group: data.group ? String(row[data.group] ?? "") : "",
      growth: growthRaw == null ? null : Number(growthRaw),
    });
  }
  return out;
}

const MARGIN = { top: 16, right: 12, bottom: 34, left: 52 };
const MAX_CATEGORIES = 20;

function BarsInner({ data, width, height }: { data: BarsData; width: number; height: number }) {
  const theme = chartTheme();
  const palette = chartPalette();
  const [tip, setTip] = useState<TipState | null>(null);
  const all = useMemo(() => parseData(data), [data]);

  const categories = useMemo(() => {
    let cats = [...new Set(all.map((d) => d.category))];
    if (data.sortX) cats = cats.sort(catCompare);
    return cats.slice(0, MAX_CATEGORIES);
  }, [all, data.sortX]);
  const groups = useMemo(() => {
    const present = [...new Set(all.map((d) => d.group))].filter((g) => g !== "");
    if (data.groupOrder) {
      const ordered = data.groupOrder.filter((g) => present.includes(g));
      return [...ordered, ...present.filter((g) => !ordered.includes(g))];
    }
    return present.sort();
  }, [all, data.groupOrder]);
  const grouped = groups.length > 1;
  const stacked = !!data.stacked && grouped;
  const data2 = all.filter((d) => categories.includes(d.category));

  // Per-category stacked totals (positive only) set the y-domain when stacking.
  const stackTotals = useMemo(() => {
    const totals: Record<string, number> = {};
    if (!stacked) return totals;
    for (const d of data2) {
      if (d.value > 0) totals[d.category] = (totals[d.category] ?? 0) + d.value;
    }
    return totals;
  }, [data2, stacked]);

  // Grouped/stacked charts carry a series legend at the TOP, so the plot needs
  // extra headroom there — keeping the legend clear of the angled x-axis labels
  // at the bottom (which was the old overlap).
  const topPad = grouped ? MARGIN.top + 22 : MARGIN.top;
  const innerW = Math.max(10, width - MARGIN.left - MARGIN.right);
  const innerH = Math.max(10, height - topPad - MARGIN.bottom);

  const xScale = useMemo(
    () => scaleBand({ domain: categories, range: [0, innerW], padding: 0.25 }),
    [categories, innerW],
  );
  const groupScale = useMemo(
    () =>
      scaleBand({
        domain: grouped ? groups : [""],
        range: [0, xScale.bandwidth()],
        padding: 0.08,
      }),
    [grouped, groups, xScale],
  );
  const yScale = useMemo(() => {
    if (stacked) {
      const hi = Math.max(1, ...Object.values(stackTotals));
      return scaleLinear({ domain: [0, hi * 1.08], range: [innerH, 0], nice: true });
    }
    const vals = data2.map((d) => d.value);
    const hi = Math.max(0, ...vals);
    const lo = Math.min(0, ...vals);
    return scaleLinear({ domain: [lo, hi * 1.08 || 1], range: [innerH, 0], nice: true });
  }, [data2, innerH, stacked, stackTotals]);

  if (data2.length === 0) return <p className="muted">No chartable rows.</p>;

  const color = (g: string) =>
    grouped ? palette[Math.max(0, groups.indexOf(g)) % palette.length] : palette[0];

  // Running offset per category so stacked segments sit on top of each other.
  const stackOffset: Record<string, number> = {};

  return (
    <div className="chart-plot" style={{ position: "relative", width, height }}>
      <svg width={width} height={height} role="img" aria-label={data.title ?? "bar chart"}>
      <Group left={MARGIN.left} top={topPad}>
        {yScale.ticks(4).map((t) => (
          <line
            key={t}
            x1={0}
            x2={innerW}
            y1={yScale(t)}
            y2={yScale(t)}
            stroke={theme.grid}
            strokeDasharray="3 3"
          />
        ))}
        {data2.map((d, i) => {
          const x0 = xScale(d.category) ?? 0;
          let bx = x0 + (groupScale(grouped ? d.group : "") ?? 0);
          let bw = groupScale.bandwidth();
          let y = yScale(Math.max(0, d.value));
          let h = Math.abs(yScale(d.value) - yScale(0));
          if (stacked) {
            // Full-width segment sitting on the running per-category offset.
            bx = x0;
            bw = xScale.bandwidth();
            const base = stackOffset[d.category] ?? 0;
            const top = base + Math.max(0, d.value);
            y = yScale(top);
            h = Math.max(0, yScale(base) - yScale(top));
            stackOffset[d.category] = top;
          }
          const showTip = () =>
            setTip({
              left: MARGIN.left + bx + bw / 2,
              top: topPad + y,
              title: d.category,
              rows: [
                {
                  label: d.group || data.measure,
                  value: formatValue(d.value, data.measure),
                  color: color(d.group),
                },
                ...(d.growth != null
                  ? [
                      {
                        label: "growth",
                        value: `${d.growth >= 0 ? "+" : ""}${d.growth.toFixed(1)}%`,
                      },
                    ]
                  : []),
              ],
            });
          return (
            <g key={i}>
              <Bar x={bx} y={y} width={bw} height={h} rx={2} fill={color(d.group)} />
              {/* Hover target: the full column for single bars (easy to hit thin
                  bars), just this bar when clustered/stacked so each group reads
                  its own. */}
              <rect
                x={grouped ? bx : x0}
                y={stacked ? y : 0}
                width={grouped ? bw : xScale.bandwidth()}
                height={stacked ? h : innerH}
                fill="transparent"
                onMouseEnter={showTip}
                onMouseMove={showTip}
                onMouseLeave={() => setTip(null)}
              />
              {d.growth != null && !grouped && (
                <text
                  x={bx + bw / 2}
                  y={y - 4}
                  textAnchor="middle"
                  fontSize={9}
                  fill={d.growth >= 0 ? theme.good : theme.bad}
                >
                  {`${d.growth >= 0 ? "+" : ""}${d.growth.toFixed(1)}%`}
                </text>
              )}
            </g>
          );
        })}
        <AxisBottom
          top={innerH}
          scale={xScale}
          stroke={theme.axis}
          tickStroke={theme.axis}
          tickLabelProps={() => ({
            fill: theme.label,
            fontSize: 10,
            textAnchor: categories.length > 6 ? "end" : "middle",
            angle: categories.length > 6 ? -30 : 0,
          })}
        />
        <AxisLeft
          scale={yScale}
          numTicks={4}
          stroke={theme.axis}
          tickStroke={theme.axis}
          tickFormat={(v) => formatValue(Number(v), data.measure)}
          tickLabelProps={() => ({
            fill: theme.label,
            fontSize: 10,
            textAnchor: "end",
            dx: -4,
            dy: 3,
          })}
        />
      </Group>
      {grouped &&
        (() => {
          // Legend along the top (clear of the angled x labels), boxed, with each
          // item spaced by its actual label length rather than a fixed pitch. Caps
          // to what fits and shows "+N" for the rest (stacked bars can have many
          // series).
          const charW = 6;
          const swatch = 9;
          const sgap = 5;
          const itemGap = 16;
          const padX = 7;
          const maxW = innerW;
          const items: { g: string; x: number; w: number }[] = [];
          let x = 0;
          let hidden = 0;
          for (let k = 0; k < groups.length; k++) {
            const g = groups[k];
            const w = swatch + sgap + g.length * charW;
            if (x + w > maxW - 34 && items.length > 0) {
              hidden = groups.length - items.length;
              break;
            }
            items.push({ g, x, w });
            x += w + itemGap;
          }
          const last = items[items.length - 1];
          const contentW = (last ? last.x + last.w : 0) + (hidden ? 30 : 0);
          const boxW = Math.min(maxW, contentW + padX * 2);
          const panel = cssVar("--panel", "#12151c");
          return (
            <Group left={MARGIN.left} top={2}>
              <rect
                x={-padX}
                y={-1}
                width={boxW}
                height={17}
                rx={4}
                fill={panel}
                fillOpacity={0.7}
                stroke={theme.axis}
              />
              {items.map(({ g, x: gx }) => (
                <g key={g} transform={`translate(${gx}, 0)`}>
                  <rect width={9} height={9} y={2} rx={2} fill={color(g)} />
                  <text x={14} y={10} fill={theme.label} fontSize={10}>
                    {g}
                  </text>
                </g>
              ))}
              {hidden > 0 && last && (
                <text x={last.x + last.w + 6} y={10} fill={theme.label} fontSize={10}>
                  +{hidden}
                </text>
              )}
            </Group>
          );
        })()}
      </svg>
      <ChartTip tip={tip} width={width} />
    </div>
  );
}

export function Bars({ data, height = 280 }: { data: BarsData; height?: number | "fill" }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const fill = height === "fill";
  return (
    <div className={fill ? "chart chart-vfill" : "chart"} ref={ref}>
      <ChartSqlButton sql={data.sql} />
      <button
        className="chart-export"
        aria-label="Export chart as PNG"
        title="Export chart as PNG"
        onClick={(e) => {
          e.stopPropagation();
          const svg = ref.current?.querySelector("svg");
          if (svg) downloadSvgAsPng(svg, data.title ?? "chart");
        }}
      >
        PNG
      </button>
      {data.title && <div className="chart-title">{data.title}</div>}
      {/* ParentSize measures via an absolutely-positioned probe — it needs an
          explicit-height parent or the chart collapses to ~0. "fill" instead
          stretches to the flexed column and reads ParentSize's height. */}
      <div style={fill ? { flex: 1, minHeight: 220 } : { height: height as number }}>
        <ParentSize debounceTime={50}>
          {({ width, height: measured }) => {
            const h = fill ? Math.max(220, measured) : (height as number);
            return width > 0 ? <BarsInner data={data} width={width} height={h} /> : null;
          }}
        </ParentSize>
      </div>
    </div>
  );
}
