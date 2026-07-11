// Theme selection: semantic tokens in styles.css switch on <html data-theme>.
// First visit follows the system preference; the Settings toggle persists a
// manual override. index.html applies the same resolution pre-paint, and this
// module owns it for the app's lifetime.
export type Theme = "dark" | "light";

const STORAGE_KEY = "app.theme";

export function getTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function setTheme(theme: Theme): void {
  localStorage.setItem(STORAGE_KEY, theme);
  document.documentElement.dataset.theme = theme;
}

/** Apply the resolved theme before first React paint. */
export function initTheme(): void {
  document.documentElement.dataset.theme = getTheme();
}
