// Ambient motion preference (s33). The night-flight canopy now runs behind
// every screen, so it needs its own off switch: prefers-reduced-motion is an
// OS-wide setting, and "I like motion, just not behind my tables" is a
// different answer. Same shape as theme.ts — a tiny external store, so the
// Settings control and the live scene can never disagree.
import { useSyncExternalStore } from "react";

const STORAGE_KEY = "app.ambient-motion";
const listeners = new Set<() => void>();

/** Default on: the scene is the product's signature, opt-out not opt-in. */
export function getAmbientMotion(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) !== "off";
  } catch {
    return true;
  }
}

export function setAmbientMotion(on: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, on ? "on" : "off");
  } catch {
    /* private mode — the session still honours the choice via the listeners */
  }
  listeners.forEach((l) => l());
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => {
    listeners.delete(onChange);
  };
}

/** The preference as reactive state; re-renders the canopy on toggle. */
export function useAmbientMotion(): boolean {
  return useSyncExternalStore(subscribe, getAmbientMotion, () => true);
}
