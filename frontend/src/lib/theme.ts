// Theme selection: semantic tokens in styles.css switch on <html data-theme>.
// First visit follows the system preference; the Settings toggle persists a
// manual override. index.html applies the same resolution pre-paint, and this
// module owns it for the app's lifetime. <html data-theme> is the single
// source of truth — every control reads it via useTheme() so the rail toggle
// and the Settings buttons never disagree.
import { useSyncExternalStore } from "react";

export type Theme = "dark" | "light";

const STORAGE_KEY = "app.theme";
const THEME_COLORS: Record<Theme, string> = { dark: "#0b0d12", light: "#f4f5f8" };
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

export function setTheme(theme: Theme): void {
  localStorage.setItem(STORAGE_KEY, theme);
  document.documentElement.dataset.theme = theme;
  syncThemeColorMeta(theme);
  listeners.forEach((l) => l());
}

export function subscribeTheme(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => listeners.delete(onChange);
}

/** The current theme as reactive state; re-renders subscribers on setTheme. */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribeTheme, getTheme);
}

/** Apply the resolved theme before first React paint. */
export function initTheme(): void {
  const t = resolveTheme();
  document.documentElement.dataset.theme = t;
  syncThemeColorMeta(t);
}
