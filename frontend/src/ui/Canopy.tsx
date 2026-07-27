// The night-flight canopy (s33) — one canvas scene, shared by the login and
// the whole app shell. Three elements, no more: a seeded starfield, a gold
// horizon curved with the planet, and a neon grid terrain flowing toward the
// viewer. It replaces the static body::before/::after gradient wash, so the
// front door and the app are demonstrably the same sky.
//
// Two intensities from one renderer:
//   login   — full strength and full device resolution; the signature element.
//   ambient — the same scene dialled to ~40% opacity, half speed, capped at 1×
//             resolution and ~30fps, behind every app screen. Panels keep their
//             solid fills, so the texture only reads through the gutters and
//             data stays full contrast.
//
// Motion is decorative and always escapable. Under prefers-reduced-motion or
// the Settings "Ambient motion" switch the loop never starts and a single
// frame is painted instead — a designed still, not a blank. The loop also
// stops whenever the tab is hidden, so a backgrounded app costs nothing.
//
// Report pages are deliberately NOT included: .answer-page exports to PNG for
// boardrooms and must stay on a flat, neutral ground (see styles.css).
import { useEffect, useRef } from "react";
import { useAmbientMotion } from "../lib/motion";
import { useMediaQuery } from "../lib/useMediaQuery";
import { useTheme } from "../lib/theme";

export type CanopyVariant = "login" | "ambient";

/** Deterministic starfield: a seeded LCG rather than Math.random, so the sky
 *  is identical across reloads and the screenshot baselines never drift. */
const STARS = (() => {
  let seed = 987654321;
  function rnd(): number {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  }
  return Array.from({ length: 220 }, () => ({
    x: rnd(),
    y: rnd() * 0.54, // upper sky only — below that the horizon owns the frame
    r: 0.5 + rnd() * 1.2,
    a: 0.2 + rnd() * 0.7,
    phase: rnd() * 6.28,
  }));
})();

type Rgb = [number, number, number];

/** Read a semantic token as an RGB triple. Tokens resolve to plain hex at
 *  computed time, so this stays cheap; anything exotic falls back rather than
 *  painting `undefined`. */
function readRgb(styles: CSSStyleDeclaration, name: string, fallback: Rgb): Rgb {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(styles.getPropertyValue(name).trim());
  if (!m) return fallback;
  const hex = m[1].length === 3 ? m[1].replace(/./g, (c) => c + c) : m[1];
  return [
    parseInt(hex.slice(0, 2), 16),
    parseInt(hex.slice(2, 4), 16),
    parseInt(hex.slice(4, 6), 16),
  ];
}

const rgba = (c: Rgb, a: number) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

/** Terrain relief: ridges rise outside a flat corridor down the middle, so the
 *  eye is led along the ground the aircraft is flying over rather than across a
 *  uniform pattern. */
function ridge(v: number): number {
  const a = Math.abs(v);
  if (a < 0.34) return 0;
  const t = (a - 0.34) / 1.1;
  return (0.55 + 0.45 * Math.sin(v * 5.3 + 2.1)) * t * t;
}

export function Canopy({ variant = "ambient" }: { variant?: CanopyVariant }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const reduced = useMediaQuery("(prefers-reduced-motion: reduce)");
  const motionOn = useAmbientMotion();
  // Not read directly — it re-runs the effect so the palette is re-sampled
  // when the theme flips (light "chart-paper" turns the starfield into a
  // dot-field, which is the right answer on paper).
  useTheme();

  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!wrap || !canvas || !ctx) return;

    const isLogin = variant === "login";
    const animate = !reduced && motionOn;
    // Half speed behind the app: the same scene, but nothing that competes
    // with the content for attention.
    const SPEED = isLogin ? 0.000028 : 0.000014;
    // The ambient layer is a blurred backdrop, so it never needs retina pixels;
    // capping resolution is what keeps a full-viewport canvas free.
    const maxDpr = isLogin ? 2.5 : 1;
    const minFrameMs = isLogin ? 0 : 33;
    const horizonAt = isLogin ? 0.58 : 0.62;
    // The ambient layer already sits at 40% opacity; dimming its stars again
    // would erase them, so the gain only trims the brightest few.
    const starGain = isLogin ? 1 : 0.8;

    const styles = getComputedStyle(document.documentElement);
    const gold = readRgb(styles, "--accent", [217, 168, 78]);
    const goldSoft = readRgb(styles, "--accent-soft", [242, 202, 121]);
    const blue = readRgb(styles, "--accent-2", [110, 168, 254]);
    const star = readRgb(styles, "--text", [214, 228, 255]);

    let dpr = 1;
    function size(): void {
      const r = wrap!.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, maxDpr);
      canvas!.width = Math.max(1, Math.round(r.width * dpr));
      canvas!.height = Math.max(1, Math.round(r.height * dpr));
    }

    let off = 0;
    let clock = 0;

    function paint(): void {
      const w = canvas!.width;
      const h = canvas!.height;
      const cx = w / 2;
      const yh = h * horizonAt;
      ctx!.clearRect(0, 0, w, h);

      // --- night sky ------------------------------------------------------
      for (const s of STARS) {
        const twinkle = 0.7 + 0.3 * Math.sin(clock * 1.6 + s.phase);
        ctx!.fillStyle = rgba(star, +(s.a * twinkle * starGain).toFixed(3));
        ctx!.beginPath();
        ctx!.arc(s.x * w, s.y * h, s.r * dpr, 0, 7);
        ctx!.fill();
      }

      // --- the horizon, curved with the planet ----------------------------
      // A very large circle read near its top: the drop across the frame is a
      // couple of percent of the height, which is exactly what an airliner's
      // horizon looks like — enough to feel round, not enough to read as a ball.
      const bigR = 6 * h;
      const horY = (x: number) => {
        const dx = x - cx;
        return yh + (bigR - Math.sqrt(Math.max(0, bigR * bigR - dx * dx)));
      };
      function horizonStroke(width: number, alpha: number, blur: number): void {
        ctx!.save();
        ctx!.strokeStyle = rgba(goldSoft, alpha);
        ctx!.lineWidth = width * dpr;
        ctx!.shadowColor = rgba(gold, 0.9);
        ctx!.shadowBlur = blur * dpr;
        ctx!.beginPath();
        for (let x = 0; x <= w + 1; x += w / 48) {
          if (x) ctx!.lineTo(x, horY(x));
          else ctx!.moveTo(x, horY(x));
        }
        ctx!.stroke();
        ctx!.restore();
      }
      horizonStroke(5, 0.1, 18); // the glow
      horizonStroke(1.1, 0.75, 9); // the line itself

      // --- neon grid terrain, flowing toward the viewer -------------------
      const halfW = w * 0.62;
      const amp = h * 0.16;
      /** v: across the ground (-1.6…1.6). d: toward the viewer (0 = horizon). */
      function pt(v: number, d: number): [number, number] {
        const x = cx + v * halfW * d;
        // The curvature flattens out as the ground comes toward the camera,
        // which is what keeps the grid welded to the horizon instead of
        // floating in front of it.
        const curve = (horY(x) - yh) * (1 - d) * (1 - d);
        return [x, yh + (h - yh) * d * d - ridge(v) * amp * d * d + curve];
      }
      function stroke(pts: [number, number][], width: number, alpha: number): void {
        ctx!.lineWidth = width * dpr;
        ctx!.strokeStyle = rgba(blue, +alpha.toFixed(3));
        ctx!.beginPath();
        for (let i = 0; i < pts.length; i++) {
          if (i) ctx!.lineTo(pts[i][0], pts[i][1]);
          else ctx!.moveTo(pts[i][0], pts[i][1]);
        }
        ctx!.stroke();
      }
      for (let k = 0; k < 15; k++) {
        const d = (k / 15 + off) % 1;
        if (d < 0.03) continue; // swallow the seam at the vanishing point
        const line: [number, number][] = [];
        for (let v = -1.6; v <= 1.601; v += 0.05) {
          const p = pt(v, d);
          if (p[0] < -40 * dpr || p[0] > w + 40 * dpr) continue;
          line.push(p);
        }
        if (line.length > 1) {
          stroke(line, 2.4, 0.04 + 0.06 * d);
          stroke(line, 1, 0.09 + 0.16 * d);
        }
      }
      for (let v = -1.6; v <= 1.601; v += 0.16) {
        const line: [number, number][] = [];
        for (let d = 0.03; d <= 1.001; d += 0.05) line.push(pt(v, d));
        stroke(line, 2.4, 0.05);
        stroke(line, 1, 0.12);
      }
    }

    size();
    paint();

    let raf = 0;
    let last = 0;
    let painted = 0;
    function frame(ts: number): void {
      const dt = last ? ts - last : 16;
      last = ts;
      off = (off + dt * SPEED) % 1;
      clock += dt * 0.001;
      if (ts - painted >= minFrameMs) {
        painted = ts;
        paint();
      }
      raf = requestAnimationFrame(frame);
    }
    function start(): void {
      if (!animate || raf) return;
      last = 0;
      painted = 0;
      raf = requestAnimationFrame(frame);
    }
    function stop(): void {
      if (!raf) return;
      cancelAnimationFrame(raf);
      raf = 0;
    }
    function onVisibility(): void {
      if (document.hidden) stop();
      else start();
    }

    const ro = new ResizeObserver(() => {
      size();
      paint();
    });
    ro.observe(wrap);
    document.addEventListener("visibilitychange", onVisibility);
    start();

    return () => {
      stop();
      ro.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [variant, reduced, motionOn]);

  return (
    <div ref={wrapRef} className={`canopy canopy-${variant}`} aria-hidden="true">
      <div className="canopy-sky" />
      <canvas ref={canvasRef} className="canopy-canvas" />
      {variant === "login" && <div className="canopy-vignette" />}
    </div>
  );
}
