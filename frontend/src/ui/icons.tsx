// Inline stroke icons — no icon dependency; sized/colored by the parent.
// All are 20px 24-viewBox stroke-current so rail/nav state colors them.
import { ReactNode, useId } from "react";

function I({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
    >
      {title && <title>{title}</title>}
      {children}
    </svg>
  );
}

// Chat — a rounded speech bubble (s17 locked nav set).
export const IconChat = () => (
  <I>
    <path d="M20.5 11.5a7.5 7.5 0 0 1-10.7 6.8L4 20l1.7-4.3A7.5 7.5 0 1 1 20.5 11.5Z" />
  </I>
);

export const IconGolden = () => (
  <I>
    <circle cx="12" cy="12" r="8" />
    <circle cx="12" cy="12" r="3.2" />
    <path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22" />
  </I>
);

export const IconSql = () => (
  <I>
    <rect x="3" y="4.5" width="18" height="15" rx="2" />
    <path d="M7 10l3 2.5L7 15" />
    <path d="M12.5 15H17" />
  </I>
);

// Explore — a compass (dataset exploration).
export const IconExplore = () => (
  <I>
    <circle cx="12" cy="12" r="9" />
    <path d="M15.5 8.5l-2 5-5 2 2-5 5-2z" />
  </I>
);

// Admin — a cockpit gauge with a needle (s17 locked nav set).
export const IconAdmin = () => (
  <I>
    <path d="M4 16a8 8 0 1 1 16 0" />
    <path d="M12 16l4.5-3.5" />
    <circle cx="12" cy="16" r="1.2" fill="currentColor" stroke="none" />
    <path d="M4.5 16h1.2M18.3 16h1.2M12 8v1.2" />
  </I>
);

export const IconSettings = () => (
  <I>
    <path d="M4 7.5h9M17.5 7.5H20" />
    <circle cx="15" cy="7.5" r="2.2" />
    <path d="M4 16.5h2.5M11 16.5h9" />
    <circle cx="8.5" cy="16.5" r="2.2" />
  </I>
);

export const IconSun = () => (
  <I>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M19.1 4.9l-1.8 1.8M6.7 17.3l-1.8 1.8" />
  </I>
);

export const IconMoon = () => (
  <I>
    <path d="M20 13.5A8 8 0 0 1 10.5 4 8 8 0 1 0 20 13.5z" />
  </I>
);

export const IconExit = () => (
  <I>
    <path d="M9.5 4H5v16h4.5" />
    <path d="M13 12h8m-3.2-3.2L21 12l-3.2 3.2" />
  </I>
);

export const IconHistory = () => (
  <I>
    <path d="M4 6h16M4 12h16M4 18h10" />
  </I>
);

export const IconStop = () => (
  <I>
    <rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" stroke="none" />
  </I>
);

export const IconSend = () => (
  <I>
    <path d="M12 19V6m-5.5 5.5L12 6l5.5 5.5" />
  </I>
);

/** The Data Pilot mark (s17): a flat filled airliner — swept wings, wing
 *  engines, tail stabilisers — angled 45° (nose upper-right), cut from the
 *  accent-ink over the accent gradient tile. Theme-aware: the tile + plane read
 *  the live --accent-soft / --accent / --accent-ink tokens, so it never floats
 *  as a hardcoded dark square on a light card (issue #11). Favicon twin lives
 *  in public/favicon.svg (standalone, hardcoded Night Flight hexes). */
export function BrandMark({ size = 30 }: { size?: number }) {
  const gid = `dp-mark-g-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" aria-hidden="true">
      <rect width="100" height="100" rx="25" fill={`url(#${gid})`} />
      <g transform="rotate(45 50 50)" fill="var(--accent-ink, #1a1204)">
        <path d="M50 15C51.5 15 53 18 53 24L53 46L52 70L51.5 82L50 86L48.5 82L48 70L47 46L47 24C47 18 48.5 15 50 15Z" />
        <path d="M52.5 40L82 60L82 65L53.5 52Z" />
        <path d="M47.5 40L18 60L18 65L46.5 52Z" />
        <path d="M51.5 72L64 80L64 84L51 78Z" />
        <path d="M48.5 72L36 80L36 84L49 78Z" />
        <path d="M63 50L68 53L66.5 57L61.5 54Z" />
        <path d="M37 50L32 53L33.5 57L38.5 54Z" />
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
