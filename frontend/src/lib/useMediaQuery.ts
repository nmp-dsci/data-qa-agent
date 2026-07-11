// Viewport tier hook: the shell renders NavRail vs BottomNav from JS (not CSS
// display toggles) so only one role="tablist" exists at a time — Playwright's
// strict getByRole stays unambiguous and screen readers see a single nav.
import { useEffect, useState } from "react";

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const onChange = () => setMatches(mq.matches);
    mq.addEventListener("change", onChange);
    setMatches(mq.matches);
    return () => mq.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

/** Mobile tier (< 768px): bottom nav + sheets instead of the icon rail. */
export const MOBILE_QUERY = "(max-width: 767px)";
