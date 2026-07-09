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
import { chartPalette, chartTheme, formatValue } from "./tokens";

export interface BarsData {
  dimension: string;
  measure: string;
  group?: string | null;
  title?: string | null;
  rows: Record<string, unknown>[];
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

  const categories = useMemo(
    () => [...new Set(all.map((d) => d.category))].slice(0, MAX_CATEGORIES),
    [all],
  );
  const groups = useMemo(
    () => [...new Set(all.map((d) => d.group))].filter((g) => g !== "").sort(),
    [all],
  );
  const grouped = groups.length > 1;
  const data2 = all.filter((d) => categories.includes(d.category));

  const innerW = Math.max(10, width - MARGIN.left - MARGIN.right);
  const innerH = Math.max(10, height - MARGIN.top - MARGIN.bottom);

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
    const vals = data2.map((d) => d.value);
    const hi = Math.max(0, ...vals);
    const lo = Math.min(0, ...vals);
    return scaleLinear({ domain: [lo, hi * 1.08 || 1], range: [innerH, 0], nice: true });
  }, [data2, innerH]);

  if (data2.length === 0) return <p className="muted">No chartable rows.</p>;

  const color = (g: string) =>
    grouped ? palette[Math.max(0, groups.indexOf(g)) % palette.length] : palette[0];

  return (
    <div className="chart-plot" style={{ position: "relative", width, height }}>
      <svg width={width} height={height} role="img" aria-label={data.title ?? "bar chart"}>
      <Group left={MARGIN.left} top={MARGIN.top}>
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
          const bx = x0 + (groupScale(grouped ? d.group : "") ?? 0);
          const bw = groupScale.bandwidth();
          const y = yScale(Math.max(0, d.value));
          const h = Math.abs(yScale(d.value) - yScale(0));
          const showTip = () =>
            setTip({
              left: MARGIN.left + bx + bw / 2,
              top: MARGIN.top + y,
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
                  bars), just this bar when clustered so each group reads its own. */}
              <rect
                x={grouped ? bx : x0}
                y={0}
                width={grouped ? bw : xScale.bandwidth()}
                height={innerH}
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
      {grouped && (
        <Group left={MARGIN.left} top={height - 6}>
          {groups.map((g, i) => (
            <g key={g} transform={`translate(${i * 110}, 0)`}>
              <rect width={9} height={9} y={-8} rx={2} fill={color(g)} />
              <text x={13} fill={theme.label} fontSize={10}>
                {g}
              </text>
            </g>
          ))}
        </Group>
      )}
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
