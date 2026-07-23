// Combo — the house "line + bar" dual-axis chart in visx: grouped bars for one
// measure on the primary (left) y-axis + a line per series for a second measure
// on a secondary (right) y-axis, sharing a nominal x. Powers the report engine's
// `compare` object when it carries a `line_measure` (e.g. sales volume as bars +
// median price as a line, by SQM band, grouped by suburb). Colors from tokens.
import { useMemo, useRef, useState } from "react";
import { AxisBottom, AxisLeft, AxisRight } from "@visx/axis";
import { Group } from "@visx/group";
import { ParentSize } from "@visx/responsive";
import { scaleBand, scaleLinear } from "@visx/scale";
import { Bar, LinePath } from "@visx/shape";
import { ChartTip, TipState } from "./ChartTip";
import { downloadSvgAsPng } from "./exportPng";
import { asRows, chartPalette, chartTheme, cssVar, formatValue } from "./tokens";

export interface ComboData {
  dimension: string; // x field (nominal — categories / bands)
  measure: string; // bar y field (primary axis)
  line_measure: string; // line y field (secondary axis)
  group?: string | null; // optional series (one bar cluster + one line per series)
  title?: string | null;
  rows: Record<string, unknown>[];
}

interface Datum {
  category: string;
  group: string;
  bar: number | null;
  line: number | null;
}

// top margin carries the legend row above the plot (issue #4 — a bottom legend
// collided with the rotated first x-axis tick).
const MARGIN = { top: 34, right: 56, bottom: 42, left: 56 };
const MAX_CATEGORIES = 20;

function parseData(data: ComboData): Datum[] {
  const out: Datum[] = [];
  for (const row of asRows(data.rows)) {
    const category = row[data.dimension];
    if (category == null) continue;
    const bar = Number(row[data.measure]);
    const line = Number(row[data.line_measure]);
    out.push({
      category: String(category),
      group: data.group ? String(row[data.group] ?? "") : "",
      bar: Number.isFinite(bar) ? bar : null,
      line: Number.isFinite(line) ? line : null,
    });
  }
  return out;
}

function ComboInner({ data, width, height }: { data: ComboData; width: number; height: number }) {
  const theme = chartTheme();
  const palette = chartPalette();
  const [tip, setTip] = useState<TipState | null>(null);
  const all = useMemo(() => parseData(data), [data]);

  // Categories + series keep first-seen order (the SQL decides the band order).
  const categories = useMemo(() => {
    const seen: string[] = [];
    for (const d of all) if (!seen.includes(d.category)) seen.push(d.category);
    return seen.slice(0, MAX_CATEGORIES);
  }, [all]);
  const groups = useMemo(() => {
    const seen: string[] = [];
    for (const d of all) if (d.group !== "" && !seen.includes(d.group)) seen.push(d.group);
    return seen;
  }, [all]);
  const grouped = groups.length > 0;
  const seriesKeys = grouped ? groups : [""];
  const rows = all.filter((d) => categories.includes(d.category));

  const innerW = Math.max(10, width - MARGIN.left - MARGIN.right);
  const innerH = Math.max(10, height - MARGIN.top - MARGIN.bottom);

  const xScale = useMemo(
    () => scaleBand({ domain: categories, range: [0, innerW], padding: 0.28 }),
    [categories, innerW],
  );
  const groupScale = useMemo(
    () => scaleBand({ domain: seriesKeys, range: [0, xScale.bandwidth()], padding: 0.12 }),
    [seriesKeys, xScale],
  );
  const yLeft = useMemo(() => {
    const vals = rows.map((d) => d.bar).filter((v): v is number => v != null);
    const hi = Math.max(0, ...vals);
    return scaleLinear({ domain: [0, hi * 1.08 || 1], range: [innerH, 0], nice: true });
  }, [rows, innerH]);
  const yRight = useMemo(() => {
    const vals = rows.map((d) => d.line).filter((v): v is number => v != null);
    if (vals.length === 0) return scaleLinear({ domain: [0, 1], range: [innerH, 0], nice: true });
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const pad = (hi - lo || Math.abs(hi) || 1) * 0.1;
    return scaleLinear({ domain: [lo - pad, hi + pad], range: [innerH, 0], nice: true });
  }, [rows, innerH]);

  if (rows.length === 0) return <p className="muted">No chartable rows.</p>;

  // Grouped: bar + line share one color per series, so a viewer matches a line
  // back to its bar cluster. Ungrouped: bar and line are distinct series on
  // their own axes, so they get distinct palette slots instead of collapsing
  // to the same color (issue: ungrouped compare charts were monochrome).
  const barColor = (g: string) =>
    grouped ? palette[Math.max(0, groups.indexOf(g)) % palette.length] : palette[0];
  const lineColor = (g: string) =>
    grouped ? palette[Math.max(0, groups.indexOf(g)) % palette.length] : palette[1 % palette.length];
  const centerX = (category: string) => (xScale(category) ?? 0) + xScale.bandwidth() / 2;

  // One line path per series over the categories that have a line value.
  const linePoints = (g: string) =>
    categories
      .map((cat) => {
        const d = rows.find((r) => r.category === cat && r.group === g);
        return d && d.line != null ? { x: centerX(cat), y: yRight(d.line) } : null;
      })
      .filter((p): p is { x: number; y: number } => p !== null);

  const showTip = (category: string) => {
    const here = rows.filter((r) => r.category === category);
    setTip({
      left: MARGIN.left + centerX(category),
      top: MARGIN.top + 8,
      title: category,
      rows: seriesKeys.flatMap((g) => {
        const d = here.find((r) => r.group === g);
        if (!d) return [];
        const label = grouped ? g : data.measure;
        return [
          {
            label: `${label} · ${data.measure}`,
            value: d.bar != null ? formatValue(d.bar, data.measure) : "—",
            color: barColor(g),
          },
          {
            label: `${label} · ${data.line_measure}`,
            value: d.line != null ? formatValue(d.line, data.line_measure) : "—",
            color: lineColor(g),
          },
        ];
      }),
    });
  };

  return (
    <div className="chart-plot" style={{ position: "relative", width, height }}>
      <svg width={width} height={height} role="img" aria-label={data.title ?? "line and bar chart"}>
        <Group left={MARGIN.left} top={MARGIN.top}>
          {yLeft.ticks(4).map((t) => (
            <line
              key={t}
              x1={0}
              x2={innerW}
              y1={yLeft(t)}
              y2={yLeft(t)}
              stroke={theme.grid}
              strokeDasharray="3 3"
            />
          ))}
          {/* Grouped bars (primary axis) */}
          {rows.map((d, i) =>
            d.bar == null ? null : (
              <Bar
                key={`b${i}`}
                x={(xScale(d.category) ?? 0) + (groupScale(d.group) ?? 0)}
                y={yLeft(Math.max(0, d.bar))}
                width={groupScale.bandwidth()}
                height={Math.abs(yLeft(d.bar) - yLeft(0))}
                rx={2}
                fill={barColor(d.group)}
                fillOpacity={0.85}
              />
            ),
          )}
          {/* One line per series (secondary axis) */}
          {seriesKeys.map((g) => {
            const pts = linePoints(g);
            if (pts.length === 0) return null;
            return (
              <g key={`l${g}`}>
                <LinePath
                  data={pts}
                  x={(p) => p.x}
                  y={(p) => p.y}
                  stroke={lineColor(g)}
                  strokeWidth={2.5}
                />
                {pts.map((p, i) => (
                  <circle
                    key={i}
                    cx={p.x}
                    cy={p.y}
                    r={3.5}
                    fill={lineColor(g)}
                    stroke={cssVar("--panel", "#0e0f13")}
                    strokeWidth={1.5}
                  />
                ))}
              </g>
            );
          })}
          {/* Hover targets: one transparent column per category */}
          {categories.map((cat) => (
            <rect
              key={`h${cat}`}
              x={xScale(cat) ?? 0}
              y={0}
              width={xScale.bandwidth()}
              height={innerH}
              fill="transparent"
              onMouseEnter={() => showTip(cat)}
              onMouseMove={() => showTip(cat)}
              onMouseLeave={() => setTip(null)}
            />
          ))}
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
            scale={yLeft}
            numTicks={4}
            stroke={theme.axis}
            tickStroke={theme.axis}
            tickFormat={(v) => formatValue(Number(v), data.measure)}
            tickLabelProps={() => ({ fill: theme.label, fontSize: 10, textAnchor: "end", dx: -4, dy: 3 })}
          />
          <AxisRight
            left={innerW}
            scale={yRight}
            numTicks={4}
            stroke={theme.axis}
            tickStroke={theme.axis}
            tickFormat={(v) => formatValue(Number(v), data.line_measure)}
            tickLabelProps={() => ({ fill: theme.label, fontSize: 10, textAnchor: "start", dx: 4, dy: 3 })}
          />
        </Group>
        {/* Legend above the plot: series (grouped, bar+line share a color per
            series) or the two measures — bar and line — when ungrouped, so a
            single-series compare chart's line is still identifiable. */}
        <Group left={MARGIN.left} top={18}>
          {(grouped
            ? groups.map((g) => ({ key: g, label: g, color: barColor(g) }))
            : [
                { key: "bar", label: data.measure, color: barColor("") },
                { key: "line", label: data.line_measure, color: lineColor("") },
              ]
          ).map((item, i) => (
            <g key={item.key} transform={`translate(${i * 120}, 0)`}>
              <rect width={9} height={9} y={-8} rx={2} fill={item.color} />
              <text x={13} fill={theme.label} fontSize={10}>
                {item.label}
              </text>
            </g>
          ))}
        </Group>
      </svg>
      <ChartTip tip={tip} width={width} />
    </div>
  );
}

export function Combo({ data, height = 280 }: { data: ComboData; height?: number | "fill" }) {
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
      <div style={fill ? { flex: 1, minHeight: 220 } : { height: height as number }}>
        <ParentSize debounceTime={50}>
          {({ width, height: measured }) => {
            const h = fill ? Math.max(220, measured) : (height as number);
            return width > 0 ? <ComboInner data={data} width={width} height={h} /> : null;
          }}
        </ParentSize>
      </div>
    </div>
  );
}
