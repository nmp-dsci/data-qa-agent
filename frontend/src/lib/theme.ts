// Theme selection: semantic tokens in styles.css switch on <html data-theme>.
// First visit follows the system preference; the Settings toggle persists a
// manual override. index.html applies the same resolution pre-paint, and this
// module owns it for the app's lifetime. <html data-theme> is the single
// source of truth — every control reads it via useTheme() so the rail toggle
// and the Settings buttons never disagree.
import { useSyncExternalStore } from "react";

export type Theme = "dark" | "light";
/** What the user chose: a fixed override, or "system" (follow the OS, no store). */
export type ThemePref = Theme | "system";

const STORAGE_KEY = "app.theme";
const THEME_COLORS: Record<Theme, string> = { dark: "#0a0d15", light: "#f6f4ee" };
const listeners = new Set<() => void>();

function resolveTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function getTheme(): Theme {
  const t = document.documentElement.dataset.theme;
  return t === "light" || t === "dark" ? t : resolveTheme();
}

function syncThemeColorMeta(theme: Theme): void {
  document
    .querySelectorAll('meta[name="theme-color"]')
    .forEach((m) => m.setAttribute("content", THEME_COLORS[theme]));
}

function applyResolved(): void {
  const applied = resolveTheme();
  document.documentElement.dataset.theme = applied;
  syncThemeColorMeta(applied);
  listeners.forEach((l) => l());
}

export function setTheme(theme: Theme): void {
  localStorage.setItem(STORAGE_KEY, theme);
  applyResolved();
}

/** The user's appearance choice: a fixed override, or "system" when none stored. */
export function getThemePref(): ThemePref {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "light" || stored === "dark" ? stored : "system";
}

/** Set the appearance preference. "system" clears the override so the app tracks
 *  the OS scheme live (see the matchMedia listener in initTheme). */
export function setThemePref(pref: ThemePref): void {
  if (pref === "system") localStorage.removeItem(STORAGE_KEY);
  else localStorage.setItem(STORAGE_KEY, pref);
  applyResolved();
}

/** The current preference as reactive state (Dark / Light / System control). */
export function useThemePref(): ThemePref {
  return useSyncExternalStore(subscribeTheme, getThemePref);
}

export function subscribeTheme(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => listeners.delete(onChange);
}

/** The current theme as reactive state; re-renders subscribers on setTheme. */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribeTheme, getTheme);
}

/** Apply the resolved theme before first React paint, and — while in "system"
 *  mode — keep tracking the OS scheme as it changes. */
export function initTheme(): void {
  const t = resolveTheme();
  document.documentElement.dataset.theme = t;
  syncThemeColorMeta(t);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (getThemePref() === "system") applyResolved();
  });
}
