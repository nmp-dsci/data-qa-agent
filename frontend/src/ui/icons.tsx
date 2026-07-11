// Inline stroke icons — no icon dependency; sized/colored by the parent.
// All are 20px 24-viewBox stroke-current so rail/nav state colors them.
import { ReactNode } from "react";

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

export const IconChat = () => (
  <I>
    <path d="M4 5.5h16v11.5H9.5L5 21v-4H4z" />
    <path d="M8.5 11h.01M12 11h.01M15.5 11h.01" strokeWidth="2.4" />
  </I>
);

export const IconSql = () => (
  <I>
    <rect x="3" y="4.5" width="18" height="15" rx="2" />
    <path d="M7 10l3 2.5L7 15" />
    <path d="M12.5 15H17" />
  </I>
);

export const IconAdmin = () => (
  <I>
    <path d="M12 3l7 2.8v5.6c0 4.2-2.9 7.2-7 9.6-4.1-2.4-7-5.4-7-9.6V5.8z" />
    <path d="M9 11.8l2.2 2.2L15.4 9.6" />
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

/** The Datapilot mark: gold diamond on a dark rounded square (favicon twin). */
export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="16" fill="#0b0d12" />
      <path
        d="M32 12 L48 32 L32 52 L16 32 Z"
        fill="none"
        stroke="url(#dp-mark-g)"
        strokeWidth="5"
        strokeLinejoin="round"
      />
      <circle cx="32" cy="32" r="4" fill="#f0c674" />
      <defs>
        <linearGradient id="dp-mark-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#f0c674" />
          <stop offset="1" stopColor="#b48a3f" />
        </linearGradient>
      </defs>
    </svg>
  );
}
