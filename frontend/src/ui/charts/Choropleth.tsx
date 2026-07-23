// Choropleth — a value shaded across postcode polygons. Renders pre-projected
// SVG paths (built offline by scripts/build_poa_paths.py) so there is NO runtime
// geo library and no new dependency; the ~72 KB layer lazy-loads only when a map
// shows. Registered as the report-engine `choropleth` object type, so agent
// reports and Goldens can emit maps too — it renders only for datasets whose
// manifest declares a geo binding.
import { useEffect, useMemo, useRef, useState } from "react";
import { asRows, cssVar } from "./tokens";

export interface ChoroplethData {
  layer: string; // e.g. "poa_nsw" -> /geo/poa_nsw.paths.json
  key_field: string; // row key holding the polygon key (postcode)
  value_field: string; // row key holding the shaded value
  title?: string | null;
  rows: Record<string, unknown>[];
  height?: number | "fill";
  /** Center the color ramp on 0 (for a Δ) instead of spanning [min,max]. */
  diverging?: boolean;
  /** Click a shape -> add its key as a filter. */
  onSelect?: (key: string) => void;
}

interface Layer {
  viewBox: [number, number];
  features: { postcode: string; d: string }[];
}

// One fetch per layer, shared across every map on the page.
const layerCache = new Map<string, Promise<Layer>>();
function loadLayer(layer: string): Promise<Layer> {
  let p = layerCache.get(layer);
  if (!p) {
    p = fetch(`/geo/${layer}.paths.json`).then((r) => {
      if (!r.ok) throw new Error(`map layer ${layer} not found`);
      return r.json() as Promise<Layer>;
    });
    layerCache.set(layer, p);
  }
  return p;
}

function hexToRgb(h: string): [number, number, number] {
  const m = h.replace("#", "");
  const n = parseInt(m.length === 3 ? m.replace(/(.)/g, "$1$1") : m, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function lerp(a: [number, number, number], b: [number, number, number], t: number): string {
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

export function Choropleth({ data }: { data: ChoroplethData }) {
  const [layer, setLayer] = useState<Layer | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number; label: string } | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  // Pan/zoom transform applied to the polygon group. k = scale, x/y = translate
  // in viewBox units.
  const [tf, setTf] = useState({ k: 1, x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);

  useEffect(() => {
    let live = true;
    loadLayer(data.layer)
      .then((l) => live && setLayer(l))
      .catch((e) => live && setErr(String(e.message ?? e)));
    return () => {
      live = false;
    };
  }, [data.layer]);

  // Reset the view when the layer changes.
  useEffect(() => setTf({ k: 1, x: 0, y: 0 }), [data.layer]);

  // Map a client point to viewBox coordinates (honours preserveAspectRatio).
  function toViewBox(clientX: number, clientY: number): { x: number; y: number } {
    const svg = svgRef.current;
    const ctm = svg?.getScreenCTM();
    if (!svg || !ctm) return { x: 0, y: 0 };
    const p = svg.createSVGPoint();
    p.x = clientX;
    p.y = clientY;
    const q = p.matrixTransform(ctm.inverse());
    return { x: q.x, y: q.y };
  }

  // Scroll to zoom, centred on the cursor. Native non-passive listener so
  // preventDefault stops the page scrolling.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    function onWheel(e: WheelEvent) {
      e.preventDefault();
      const p = toViewBox(e.clientX, e.clientY);
      setTf((cur) => {
        const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
        const k = Math.min(8, Math.max(1, cur.k * factor));
        // Keep the point under the cursor fixed.
        const x = p.x - ((p.x - cur.x) / cur.k) * k;
        const y = p.y - ((p.y - cur.y) / cur.k) * k;
        return k === 1 ? { k: 1, x: 0, y: 0 } : { k, x, y };
      });
    }
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, [layer]);

  const values = useMemo(() => {
    const m = new Map<string, number>();
    for (const row of asRows(data.rows)) {
      const key = row[data.key_field];
      const v = Number(row[data.value_field]);
      if (key != null && Number.isFinite(v)) m.set(String(key), v);
    }
    return m;
  }, [data.rows, data.key_field, data.value_field]);

  const scale = useMemo(() => {
    const vals = [...values.values()];
    if (vals.length === 0) return null;
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const bad = hexToRgb(cssVar("--bad", "#f2777a"));
    const warn = hexToRgb(cssVar("--warn", "#e0af68"));
    const good = hexToRgb(cssVar("--good", "#9ece6a"));
    const abs = Math.max(Math.abs(lo), Math.abs(hi)) || 1;
    return (v: number): string => {
      const t = data.diverging ? (v / abs + 1) / 2 : hi === lo ? 0.5 : (v - lo) / (hi - lo);
      const c = Math.max(0, Math.min(1, t));
      return c <= 0.5 ? lerp(bad, warn, c * 2) : lerp(warn, good, (c - 0.5) * 2);
    };
  }, [values, data.diverging]);

  const muted = cssVar("--chart-grid", "#1d2434");
  const stroke = cssVar("--chart-axis", "#242b3d");
  const height = data.height === "fill" || data.height == null ? 260 : data.height;

  if (err) return <p className="muted">Map unavailable: {err}</p>;
  if (!layer) return <div className="skel" style={{ height }} />;

  const [w, h] = layer.viewBox;

  return (
    <div className="chart choro-wrap" ref={wrapRef} style={{ position: "relative" }}>
      {data.title && <div className="chart-title">{data.title}</div>}
      <svg
        ref={svgRef}
        viewBox={`0 0 ${w} ${h}`}
        width="100%"
        height={height}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={data.title ?? "map"}
        style={{ cursor: drag.current ? "grabbing" : tf.k > 1 ? "grab" : "default", touchAction: "none" }}
        onPointerDown={(e) => {
          if (tf.k <= 1) return;
          drag.current = { x: e.clientX, y: e.clientY, ox: tf.x, oy: tf.y };
          (e.target as Element).setPointerCapture?.(e.pointerId);
        }}
        onPointerMove={(e) => {
          // Capture the drag origin locally — it can be cleared (pointerup) before
          // the setTf updater runs, so never dereference drag.current inside it.
          const d = drag.current;
          if (!d) return;
          const a = toViewBox(e.clientX, e.clientY);
          const b = toViewBox(d.x, d.y);
          setTf((cur) => ({ ...cur, x: d.ox + (a.x - b.x), y: d.oy + (a.y - b.y) }));
        }}
        onPointerUp={() => (drag.current = null)}
        onDoubleClick={() => setTf({ k: 1, x: 0, y: 0 })}
      >
        <g transform={`translate(${tf.x} ${tf.y}) scale(${tf.k})`}>
          {layer.features.map((f) => {
            const v = values.get(f.postcode);
            const fill = v != null && scale ? scale(v) : muted;
            return (
              <path
                key={f.postcode}
                d={f.d}
                fill={fill}
                stroke={stroke}
                strokeWidth={0.4 / tf.k}
                style={{ cursor: data.onSelect ? "pointer" : "inherit" }}
                onMouseEnter={(e) => {
                  const box = wrapRef.current?.getBoundingClientRect();
                  setTip({
                    x: e.clientX - (box?.left ?? 0),
                    y: e.clientY - (box?.top ?? 0),
                    label: v != null ? `${f.postcode}: ${v.toLocaleString()}` : `${f.postcode}: —`,
                  });
                }}
                onMouseLeave={() => setTip(null)}
                onClick={() => !drag.current && data.onSelect?.(f.postcode)}
              />
            );
          })}
        </g>
      </svg>
      {tip && (
        <div className="choro-tip" style={{ left: tip.x + 10, top: tip.y + 10 }}>
          {tip.label}
        </div>
      )}
    </div>
  );
}
