// The Flight Deck kit (s25) — the five primitives the cockpit brand is built
// from, extracted once so every surface consumes them instead of re-rolling
// markup. The CSS half lives in styles.css under "Flight Deck kit"; the two
// halves are meant to be read together.
//
//   1 · PlaneGlyph        — ui/icons.tsx (it ships with the mark it's cut from)
//   2 · FlightPath        — route + waypoints + optional flying Sortie
//   3 · HudBox            — corner-ticked readout frame
//   4 · Annunciator       — cockpit status lamp
//   5 · InstrumentLabel   — the mono-caps voice
//
// Voice rule, enforced by convention rather than code: instrument type is for
// labels, telemetry and section markers. Prose never wears the costume, and
// aviation words never reach buttons — "Run query", never "Take off".
import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { PLANE_PATH_D } from "./icons";

// ---------------------------------------------------------------------------
// Path helpers
// ---------------------------------------------------------------------------

/** A smooth cubic path through the given points (Catmull-Rom → Bézier), so a
 *  route can be generated for any number of stops instead of hand-authored.
 *  The stops land exactly on the curve, which is what lets the Sortie park on
 *  a waypoint rather than near it. */
export function smoothPath(pts: { x: number; y: number }[]): string {
  if (pts.length === 0) return "";
  if (pts.length === 1) return `M${pts[0].x} ${pts[0].y}`;
  const at = (i: number) => pts[Math.max(0, Math.min(pts.length - 1, i))];
  let d = `M${pts[0].x} ${pts[0].y}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = at(i - 1), p1 = at(i), p2 = at(i + 1), p3 = at(i + 2);
    const c1 = { x: p1.x + (p2.x - p0.x) / 6, y: p1.y + (p2.y - p0.y) / 6 };
    const c2 = { x: p2.x - (p3.x - p1.x) / 6, y: p2.y - (p3.y - p1.y) / 6 };
    d += ` C${c1.x} ${c1.y} ${c2.x} ${c2.y} ${p2.x} ${p2.y}`;
  }
  return d;
}

/** Where each of `points` sits along `path`, as a 0–1 fraction of its length.
 *  Measured from the real path element once it mounts (rather than assumed)
 *  so the flying glyph and the lit contrail agree with the waypoint dots to
 *  the pixel. Sampling is fixed-step and deterministic, so visual baselines
 *  don't drift between runs. */
export function useRouteFractions(
  pathRef: React.RefObject<SVGPathElement | null>,
  points: { x: number; y: number }[],
): number[] {
  const key = useMemo(() => points.map((p) => `${p.x},${p.y}`).join(" "), [points]);
  const fallback = useMemo(
    () => points.map((_, i) => (points.length > 1 ? i / (points.length - 1) : 0)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key],
  );
  const [fracs, setFracs] = useState<number[]>(fallback);

  useEffect(() => {
    const path = pathRef.current;
    if (!path || typeof path.getTotalLength !== "function") return;
    const total = path.getTotalLength();
    if (!total) return;
    const STEPS = 400; // ≈0.25% precision — visually exact
    const samples: { len: number; x: number; y: number }[] = [];
    for (let i = 0; i <= STEPS; i++) {
      const len = (total * i) / STEPS;
      const p = path.getPointAtLength(len);
      samples.push({ len, x: p.x, y: p.y });
    }
    const pts = key ? key.split(" ").map((s) => {
      const [x, y] = s.split(",").map(Number);
      return { x, y };
    }) : [];
    setFracs(
      pts.map((w) => {
        let best = samples[0];
        let bestD = Infinity;
        for (const s of samples) {
          const d = (s.x - w.x) ** 2 + (s.y - w.y) ** 2;
          if (d < bestD) { bestD = d; best = s; }
        }
        return best.len / total;
      }),
    );
  }, [pathRef, key]);

  return fracs;
}

// ---------------------------------------------------------------------------
// 2 · FlightPath
// ---------------------------------------------------------------------------

export type FlightStop = { key: string; label: string; note?: ReactNode };

/** A route across a row of stops, lit up to `active`, with the Sortie parked
 *  on the current one. The SVG keeps a uniform aspect ratio (the plane must
 *  never squash), so stop x-positions scale with the container and the HTML
 *  labels below can be placed by percentage and stay aligned.
 *
 *  Used by: the chat hero's flight plan, the chat streaming strip, and the
 *  Explore empty state (with `flying={false}` for a parked plane). */
export function FlightPath({
  stops,
  active,
  flying = true,
  className = "",
  labels = true,
}: {
  stops: FlightStop[];
  /** Index of the stop the Sortie is on; -1 lights nothing. */
  active: number;
  /** Show the Sortie on the route. False parks the route empty. */
  flying?: boolean;
  className?: string;
  /** Render the HTML label row under the route. */
  labels?: boolean;
}) {
  const VB_W = 1000;
  const VB_H = 70;
  const routeRef = useRef<SVGPathElement>(null);
  const n = stops.length;

  // A climb profile: each stop sits a little higher than the last, so the row
  // reads as a departure rather than a progress bar.
  const points = useMemo(
    () =>
      stops.map((_, i) => ({
        x: n > 1 ? (VB_W * (i + 0.5)) / n : VB_W / 2,
        y: n > 1 ? 54 - (34 * i) / (n - 1) : 36,
      })),
    [stops, n],
  );
  const d = useMemo(() => smoothPath(points), [points]);
  const fracs = useRouteFractions(routeRef, points);
  const lit = active >= 0 ? fracs[Math.min(active, n - 1)] ?? 0 : 0;

  return (
    <div className={`flightpath ${className}`.trim()}>
      <svg
        className="fp-svg"
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        preserveAspectRatio="xMidYMid meet"
        aria-hidden="true"
      >
        <path className="fp-route" ref={routeRef} d={d} />
        {active >= 0 && (
          <path
            className="fp-lit"
            d={d}
            pathLength={100}
            strokeDasharray="100"
            strokeDashoffset={100 - lit * 100}
          />
        )}
        {points.map((p, i) => (
          <circle
            key={stops[i].key}
            className={i <= active ? "fp-wp lit" : "fp-wp"}
            cx={p.x}
            cy={p.y}
            r="7"
          />
        ))}
        {flying && active >= 0 && (
          <g
            className="fp-plane"
            style={{
              offsetPath: `path("${d}")`,
              offsetRotate: "auto",
              offsetDistance: `${lit * 100}%`,
            }}
          >
            <g transform="rotate(90) scale(0.30) translate(-50,-50)">
              <path d={PLANE_PATH_D} />
            </g>
          </g>
        )}
      </svg>
      {labels && (
        <ol className="fp-labels">
          {stops.map((s, i) => (
            <li
              key={s.key}
              className={i < active ? "fp-label done" : i === active ? "fp-label on" : "fp-label"}
              aria-current={i === active ? "step" : undefined}
            >
              <span className="instrument-label">{s.label}</span>
              {s.note && <span className="fp-label-note">{s.note}</span>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 3 · HudBox — a corner-ticked readout frame
// ---------------------------------------------------------------------------

/** A boxed instrument readout. `label` is the mono-caps caption, `value` the
 *  number (always tabular). `lit` pins the corner ticks to accent instead of
 *  waiting for hover. Children render under the value (deltas, sublines). */
export function HudBox({
  label,
  value,
  lit = false,
  className = "",
  children,
}: {
  label?: ReactNode;
  value?: ReactNode;
  lit?: boolean;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <div className={`hud-box${lit ? " lit" : ""} ${className}`.trim()}>
      {label != null && <span className="hud-box-label">{label}</span>}
      {value != null && <span className="hud-box-value">{value}</span>}
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 4 · Annunciator — a cockpit status lamp
// ---------------------------------------------------------------------------

export type LampState = "off" | "on" | "warn" | "bad" | "accent";

/** A status lamp: a dot plus a mono-caps word. These carry guarantees (RLS,
 *  AUDIT) and live states (PASS/FAIL, service health) — never decoration. */
export function Annunciator({
  children,
  state = "on",
  title,
}: {
  children: ReactNode;
  state?: LampState;
  title?: string;
}) {
  return (
    <span className={state === "off" ? "annunciator" : `annunciator ${state}`} title={title}>
      {children}
    </span>
  );
}

/** Row wrapper — just the flex/gap, but it keeps the class name in one place. */
export function Annunciators({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`annunciators ${className}`.trim()}>{children}</div>;
}

// ---------------------------------------------------------------------------
// 5 · InstrumentLabel — the mono-caps voice
// ---------------------------------------------------------------------------

export function InstrumentLabel({
  children,
  tone,
  className = "",
}: {
  children: ReactNode;
  /** dim = quiet section marker · hud = live telemetry · accent = brand metal */
  tone?: "dim" | "hud" | "accent";
  className?: string;
}) {
  return (
    <span className={`instrument-label${tone ? ` ${tone}` : ""} ${className}`.trim()}>
      {children}
    </span>
  );
}
