// Mobile shell chrome (< 768px): slim top bar (brand · context action · sign
// out) and a thumb-reach bottom tab bar. Rendered instead of NavRail, never
// alongside it, so role=tab stays unambiguous.
import { ReactNode } from "react";
import { LogOut } from "lucide-react";
import { User } from "../lib/api";
import { BrandMark } from "../ui/icons";
import { navItems, View } from "./NavRail";

/** Thumb-bar labels: eight admin tabs have to share the width, so the long
 *  ones are clipped to the word people actually say. */
const SHORT: Record<string, string> = {
  "SQL Editor": "SQL",
  "Golden Examples": "Goldens",
  Evaluations: "Evals",
  Operations: "Ops",
};

export function MobileTopBar({
  user,
  onSignOut,
  action,
}: {
  user: User;
  onSignOut: () => void;
  /** View-specific control, e.g. the chat history sheet trigger. */
  action?: ReactNode;
}) {
  return (
    <header className="mobile-top">
      <div className="mobile-brand">
        <BrandMark size={24} />
        <strong>Data Pilot</strong>
      </div>
      <div className="mobile-top-actions">
        {action}
        <button
          className="rail-item"
          aria-label="Sign out"
          title={`Sign out (${user.display_name})`}
          onClick={onSignOut}
        >
          <LogOut size={20} strokeWidth={1.8} />
        </button>
      </div>
    </header>
  );
}

export function BottomNav({
  view,
  setView,
  isAdmin,
}: {
  view: View;
  setView: (v: View) => void;
  isAdmin: boolean;
}) {
  return (
    <nav className="bottom-nav" role="tablist" aria-label="App sections">
      {navItems(isAdmin).map((item) => (
        <button
          key={item.view}
          role="tab"
          aria-selected={view === item.view}
          // The visible text is abbreviated to fit a thumb bar; the accessible
          // name stays the full label, so a selector written against the
          // desktop rail matches the mobile bar too.
          aria-label={item.label}
          className={view === item.view ? "bnav-item active" : "bnav-item"}
          onClick={() => setView(item.view)}
        >
          <item.icon />
          <span>{SHORT[item.label] ?? item.label}</span>
        </button>
      ))}
    </nav>
  );
}
