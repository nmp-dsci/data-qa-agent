// Theme selection: semantic tokens in styles.css switch on <html data-theme>.
// Dark is the default (the app's original look). The Settings tab exposes the
// toggle; this module owns persistence + application so it can run pre-React.
export type Theme = "dark" | "light";

const STORAGE_KEY = "app.theme";

export function getTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "light" ? "light" : "dark";
}

export function setTheme(theme: Theme): void {
  localStorage.setItem(STORAGE_KEY, theme);
  document.documentElement.dataset.theme = theme;
}

/** Apply the persisted theme before first paint. */
export function initTheme(): void {
  document.documentElement.dataset.theme = getTheme();
}
