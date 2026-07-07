import { useEffect, useRef } from "react";
import embed, { type VisualizationSpec } from "vega-embed";

const DARK_CHART_CONFIG = {
  background: "transparent",
  title: { color: "#e7e9ee", subtitleColor: "#9aa3b2" },
  axis: {
    labelColor: "#e7e9ee",
    titleColor: "#e7e9ee",
    gridColor: "#2a2f3a",
    domainColor: "#9aa3b2",
    tickColor: "#9aa3b2",
  },
  legend: {
    labelColor: "#e7e9ee",
    titleColor: "#e7e9ee",
    symbolStrokeColor: "#e7e9ee",
  },
  header: {
    labelColor: "#e7e9ee",
    titleColor: "#e7e9ee",
  },
  view: { stroke: "transparent" },
};

function objectConfig(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function withDarkChartTheme(spec: Record<string, unknown>): Record<string, unknown> {
  const config = objectConfig(spec.config);
  return {
    ...spec,
    background: "transparent",
    config: {
      ...config,
      ...DARK_CHART_CONFIG,
      title: { ...DARK_CHART_CONFIG.title, ...objectConfig(config.title) },
      axis: { ...DARK_CHART_CONFIG.axis, ...objectConfig(config.axis) },
      legend: { ...DARK_CHART_CONFIG.legend, ...objectConfig(config.legend) },
      header: { ...DARK_CHART_CONFIG.header, ...objectConfig(config.header) },
      view: { ...DARK_CHART_CONFIG.view, ...objectConfig(config.view) },
    },
  };
}

export function VegaChart({ spec }: { spec: Record<string, unknown> }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    let cancelled = false;
    let cleanup: (() => void) | undefined;
    embed(ref.current, withDarkChartTheme(spec) as VisualizationSpec, { actions: false })
      .then((result) => {
        if (cancelled) result.finalize();
        else cleanup = () => result.finalize();
      })
      .catch((e) => console.error("chart render failed", e));
    return () => {
      cancelled = true;
      cleanup?.();
    };
  }, [spec]);

  return <div className="chart" ref={ref} />;
}
