// s38: "flag, don't hide". Wraps an LLM-backed control so it stays visible in
// the demo but inert, wearing the ◆ chip whose title carries the full sentence.
// The server refuses the same calls with 501 not_available_demo regardless —
// this wrapper is honesty, not enforcement.
import { ReactNode } from "react";
import { isDemoMode } from "../lib/auth";

const CHIP_TITLE =
  "Runs a live LLM in the full build — this demo replays recorded answers. Everything works in dev.";

export function DemoGate({ children }: { children: ReactNode }) {
  if (!isDemoMode()) return <>{children}</>;
  return (
    <span className="demo-gate">
      <span className="demo-gate-body" aria-disabled="true">
        {children}
      </span>
      <span className="demo-chip" title={CHIP_TITLE}>
        Not available — demo only
      </span>
    </span>
  );
}
