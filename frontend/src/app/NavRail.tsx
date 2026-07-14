// Desktop shell chrome: a 56px icon rail — brand on top, section tabs in the
// middle (role=tab + aria-label keeps the e2e getByRole("tab") contract from
// the old tab bar), theme/user/sign-out at the foot.
import { ReactElement } from "react";
import { User } from "../lib/api";
import { setTheme, Theme, useTheme } from "../lib/theme";
import {
  BrandMark,
  IconAdmin,
  IconChat,
  IconExit,
  IconGolden,
  IconMoon,
  IconSettings,
  IconSql,
  IconSun,
} from "../ui/icons";

export type View = "chat" | "sql" | "goldens" | "admin" | "settings";

const ITEMS: { view: View; label: string; icon: () => ReactElement; adminOnly?: boolean }[] = [
  { view: "chat", label: "Chat", icon: IconChat },
  { view: "sql", label: "SQL Editor", icon: IconSql },
  { view: "goldens", label: "Golden Examples", icon: IconGolden, adminOnly: true },
  { view: "admin", label: "Admin", icon: IconAdmin, adminOnly: true },
  { view: "settings", label: "Settings", icon: IconSettings },
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
      {theme === "dark" ? <IconSun /> : <IconMoon />}
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
      <div className="rail-brand" title="Datapilot">
        <BrandMark size={30} />
      </div>
      <div className="rail-tabs" role="tablist" aria-orientation="vertical" aria-label="App sections">
        {navItems(user.role === "admin").map((item) => (
          <button
            key={item.view}
            role="tab"
            aria-selected={view === item.view}
            aria-label={item.label}
            title={item.label}
            className={view === item.view ? "rail-item active" : "rail-item"}
            onClick={() => setView(item.view)}
          >
            <item.icon />
          </button>
        ))}
      </div>
      <div className="rail-foot">
        <ThemeToggle className="rail-item" />
        <button
          className="rail-item"
          aria-label="Sign out"
          title={`Sign out (${user.display_name})`}
          onClick={onSignOut}
        >
          <IconExit />
        </button>
        <div className="rail-avatar" title={`${user.display_name} · ${user.role}`}>
          {initials(user.display_name)}
        </div>
      </div>
    </nav>
  );
}
