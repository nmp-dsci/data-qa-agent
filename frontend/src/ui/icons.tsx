// Brand marks only. Every generic glyph in this app is a lucide-react icon
// (s33 doctrine) — what stays here is the artwork that IS the brand and has no
// library equivalent: the airliner path, the Sortie that flies it, and the
// Data Pilot mark itself.
import { useId } from "react";

/** The airliner path from the Data Pilot mark, in the mark's own 100×100 space.
 *  Drawn nose-up (fuselage runs y=15 → y=86); the tile rotates it 45°, the
 *  PlaneGlyph rotates it 90° to fly nose-right. Shared by both. */
export const PLANE_PATH_D = [
  "M50 15C51.5 15 53 18 53 24L53 46L52 70L51.5 82L50 86L48.5 82L48 70L47 46L47 24C47 18 48.5 15 50 15Z",
  "M52.5 40L82 60L82 65L53.5 52Z",
  "M47.5 40L18 60L18 65L46.5 52Z",
  "M51.5 72L64 80L64 84L51 78Z",
  "M48.5 72L36 80L36 84L49 78Z",
  "M63 50L68 53L66.5 57L61.5 54Z",
  "M37 50L32 53L33.5 57L38.5 54Z",
].join(" ");

/** PlaneGlyph (s25) — the Sortie. The mark's airliner inverted out of its gold
 *  tile into a bare `currentColor` silhouette, so it can fly across scenes,
 *  idle beside a composer, or sit inside a button. Nose points right at 0°;
 *  callers rotate or drive it along a path. `bold` thickens the silhouette
 *  with a stroke rather than a second path, so the shape stays identical. */
export function PlaneGlyph({
  size = 22,
  bold = false,
  className,
}: {
  size?: number;
  bold?: boolean;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      className={className}
      aria-hidden="true"
      fill="currentColor"
    >
      <g
        transform="rotate(90 50 50)"
        stroke={bold ? "currentColor" : undefined}
        strokeWidth={bold ? 4 : undefined}
        strokeLinejoin={bold ? "round" : undefined}
      >
        <path d={PLANE_PATH_D} />
      </g>
    </svg>
  );
}

/** The Data Pilot mark (s33 · "night-flight horizon"): the login scene reduced
 *  to a mark — stars above a curved horizon over a grid running to the
 *  vanishing point, cut from accent-ink on the accent gradient tile. It
 *  replaces the s17 airliner, which said "aviation" but not "data"; this one
 *  is literally the product's own background, so the mark and every screen
 *  behind it are the same picture.
 *
 *  Drawn at 64 units with deliberately heavy strokes: the mark ships at 24px
 *  in the mobile bar, where a hairline grid would turn to mud. Theme-aware —
 *  the tile and the ink read live --accent-soft / --accent / --accent-ink, so
 *  it never floats as a hardcoded dark square on a light card (issue #11).
 *  Favicon twin lives in public/favicon.svg (standalone, hardcoded Night
 *  Flight hexes) and is hand-synced with this geometry. */
export function BrandMark({ size = 30 }: { size?: number }) {
  const gid = `dp-mark-g-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="16" fill={`url(#${gid})`} />
      <g fill="var(--accent-ink, #1a1204)">
        <circle cx="15" cy="15" r="2" />
        <circle cx="30" cy="10" r="1.5" />
        <circle cx="45" cy="16" r="1.8" />
        <circle cx="51" cy="25" r="1.3" />
      </g>
      <g
        stroke="var(--accent-ink, #1a1204)"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {/* the horizon — the one line the whole scene hangs on */}
        <path d="M5 34.5 Q32 27.5 59 34.5" strokeWidth="3" />
        {/* rails converging on the vanishing point */}
        <g strokeWidth="1.7" opacity=".85">
          <path d="M32 34 6 59M32 34 19 59M32 34v25M32 34l13 25M32 34l26 25" />
        </g>
        {/* two recede lines, spaced by the same squared ramp as the scene */}
        <g strokeWidth="1.6" opacity=".7">
          <path d="M12 44.5Q32 39.5 52 44.5M6.5 53Q32 47 57.5 53" />
        </g>
      </g>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--accent-soft, #f2ca79)" />
          <stop offset="1" stopColor="var(--accent, #d9a84e)" />
        </linearGradient>
      </defs>
    </svg>
  );
}
