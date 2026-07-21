import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@fontsource-variable/inter";
import "@fontsource/jetbrains-mono";
import "@fontsource/jetbrains-mono/500.css";
import App from "./app/App.tsx";
import { initTheme } from "./lib/theme";
import "./styles.css";

initTheme();

// A deploy replaces the hashed chunk files this running bundle points at, so
// the first lazy import after a deploy (Explore / SQL editor / Choropleth) can
// fail: the old chunk URL falls through to the SPA fallback and comes back as
// index.html-with-200, which the module loader rejects ("Failed to fetch
// dynamically imported module") — in prod the tab just refused to open
// (incident, 2026-07-21). Vite reports exactly this as `vite:preloadError`;
// one automatic reload picks up the new index.html and the current chunks.
// The sessionStorage guard stops a reload loop if the failure is anything
// other than a stale deploy.
window.addEventListener("vite:preloadError", (event) => {
  const key = "chunk-reload-at";
  const last = Number(sessionStorage.getItem(key) ?? 0);
  if (Date.now() - last > 30_000) {
    sessionStorage.setItem(key, String(Date.now()));
    event.preventDefault(); // suppress the unhandled rejection — we're handling it
    window.location.reload();
  }
});

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 30_000, refetchOnWindowFocus: false },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
