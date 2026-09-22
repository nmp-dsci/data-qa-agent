// s38: "flag, don't hide". Wraps an LLM-backed control so it stays visible in
// the demo but inert, wearing the ◆ chip whose title carries the full sentence.
// The server refuses the same calls with 501 not_available_demo regardless —
// this wrapper is honesty, not enforcement.
import { ReactNode } from "react";
import { isDemoMode } from "../lib/auth";

const CHIP_TITLE =
  "Runs a live LLM in the full build — this demo replays recorded answers. Everything works in dev.";

export function DemoGate({
  children,
  title = CHIP_TITLE,
}: {
  children: ReactNode;
  /** Override the chip's hover sentence for controls that are gated for a
   *  reason other than the LLM (s52: the demo has no warehouse either). */
  title?: string;
}) {
  if (!isDemoMode()) return <>{children}</>;
  return (
    // A <div>, not a <span>: fieldset is flow content, not phrasing content,
    // so it can't legally nest inside an inline span (browsers would hoist
    // it out during parsing and break the layout).
    <div className="demo-gate">
      {/* A real <fieldset disabled>, not just CSS: pointer-events:none alone
          doesn't stop keyboard activation (Enter/Space) or assistive-tech
          "activate" actions on nested controls. disabled propagates to every
          form control inside (button, input, select) — same protection the
          server-side 501 gives, but visible to keyboard and screen-reader
          users too, not just mouse users. */}
      <fieldset disabled className="demo-gate-body" aria-disabled="true">
        {children}
      </fieldset>
      <span className="demo-chip" title={title}>
        Not available — demo only
      </span>
    </div>
  );
}
