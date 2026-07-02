import { useEffect, useRef } from "react";
import embed, { type VisualizationSpec } from "vega-embed";

export function VegaChart({ spec }: { spec: Record<string, unknown> }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    let cancelled = false;
    let cleanup: (() => void) | undefined;
    embed(ref.current, spec as VisualizationSpec, { actions: false })
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
