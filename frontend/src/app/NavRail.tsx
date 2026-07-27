// Desktop shell chrome: a 56px icon rail that expands to 196px on hover, so
// the icons stay a compact permanent home and the names are one mouse-move
// away instead of a memory test. The expansion is an OVERLAY — the rail's
// footprint stays 56px and the panel grows over the content — because views
// must never reflow under a pointer that is only passing through.
//
// Icons are lucide-react (s33 doctrine: library glyphs for generic actions,
// hand-drawn only for the brand mark), sized 20/stroke 1.8 to match the rest
// of the cockpit's line weight. Evaluations finally has its own glyph: it
// reused the Goldens reticle for a year, which made the two admin tabs
// indistinguishable at a glance.
//
// The e2e contract is unchanged: every item is still role=tab with an
// aria-label, so `getByRole("tab", { name })` keeps working — aria-label wins
// over the now-visible text, so the accessible name is exactly the label.
import { ReactElement } from "react";
import {
  Compass,
  Gauge,
  LogOut,
  MessageSquare,
  Moon,
  Radar,
  Settings,
  ShieldCheck,
  SquareTerminal,
  Star,
  Sun,
} from "lucide-react";
import { User } from "../lib/api";
import { setTheme, Theme, useTheme } from "../lib/theme";
import { BrandMark } from "../ui/icons";

export type View =
  | "chat" | "explore" | "sql" | "goldens" | "evals" | "ops" | "admin" | "settings";

/** One size/weight for every rail glyph — lucide defaults to 24/2, which reads
 *  a step heavier than the cockpit's own line work. */
const GLYPH = { size: 20, strokeWidth: 1.8 } as const;

const ITEMS: { view: View; label: string; icon: () => ReactElement; adminOnly?: boolean }[] = [
  { view: "chat", label: "Chat", icon: () => <MessageSquare {...GLYPH} /> },
  { view: "explore", label: "Explore", icon: () => <Compass {...GLYPH} /> },
  { view: "sql", label: "SQL Editor", icon: () => <SquareTerminal {...GLYPH} /> },
  { view: "goldens", label: "Golden Examples", icon: () => <Star {...GLYPH} />, adminOnly: true },
  // Sits next to Golden Examples: goldens are the specification, Evaluations is
  // the score against it (s24 M4) — hence a gauge.
  { view: "evals", label: "Evaluations", icon: () => <Gauge {...GLYPH} />, adminOnly: true },
  // Sits beside Evaluations for the same reason: Evaluations is "is the answer
  // right?", Operations is "is the service healthy, safe, fast and affordable?"
  // (s32). Renamed from "Ops" in s33 — the rail shows names now, and an
  // abbreviation that saved nothing was the only one in the list.
  { view: "ops", label: "Operations", icon: () => <Radar {...GLYPH} />, adminOnly: true },
  { view: "admin", label: "Admin", icon: () => <ShieldCheck {...GLYPH} />, adminOnly: true },
  { view: "settings", label: "Settings", icon: () => <Settings {...GLYPH} /> },
];

export function navItems(isAdmin: boolean) {
  return ITEMS.filter((i) => !i.adminOnly || isAdmin);
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}

export function ThemeToggle({ className }: { className: string }) {
  const theme = useTheme();
  const next: Theme = theme === "dark" ? "light" : "dark";
  return (
    <button
      className={className}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      onClick={() => setTheme(next)}
    >
      {theme === "dark" ? <Sun {...GLYPH} /> : <Moon {...GLYPH} />}
      <span className="rail-label">{next === "light" ? "Day" : "Night"}</span>
    </button>
  );
}

export function NavRail({
  view,
  setView,
  user,
  onSignOut,
}: {
  view: View;
  setView: (v: View) => void;
  user: User;
  onSignOut: () => void;
}) {
  return (
    <nav className="rail">
      {/* focus-within expands too: a keyboard user tabbing the rail gets the
          same names a mouse user gets. */}
      <div className="rail-inner">
        <div className="rail-brand">
          <BrandMark size={30} />
          <span className="rail-label rail-wordmark">Data Pilot</span>
        </div>
        <div
          className="rail-tabs"
          role="tablist"
          aria-orientation="vertical"
          aria-label="App sections"
        >
          {navItems(user.role === "admin").map((item) => (
            <button
              key={item.view}
              role="tab"
              aria-selected={view === item.view}
              aria-label={item.label}
              className={view === item.view ? "rail-item active" : "rail-item"}
              onClick={() => setView(item.view)}
            >
              <item.icon />
              <span className="rail-label">{item.label}</span>
            </button>
          ))}
        </div>
        <div className="rail-foot">
          <ThemeToggle className="rail-item" />
          <button className="rail-item" aria-label="Sign out" onClick={onSignOut}>
            <LogOut {...GLYPH} />
            <span className="rail-label">Sign out</span>
          </button>
          <div className="rail-who" title={`${user.display_name} · ${user.role}`}>
            <span className="rail-avatar">{initials(user.display_name)}</span>
            <span className="rail-label rail-whoami">
              <strong>{user.display_name}</strong>
              <small>{user.role}</small>
            </span>
          </div>
        </div>
      </div>
    </nav>
  );
}
