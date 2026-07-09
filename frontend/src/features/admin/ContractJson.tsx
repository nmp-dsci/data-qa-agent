// ContractJson — the Template Studio inspector: the exact Page JSON the
// data-agent sends the frontend to render the page above it. Bulky row arrays
// are elided in the display (structure is the point); Copy grabs the full
// contract. This is how the Studio teaches "Data-Agent informs the frontend".
import { useMemo, useState } from "react";
import type { Page } from "../../lib/api";

/** Elide long rows/series arrays for display; keep everything else verbatim. */
function displayPage(page: Page): unknown {
  return {
    ...page,
    columns: page.columns.map((col) =>
      col.map((o) => {
        const data: Record<string, unknown> = { ...o.data };
        for (const key of ["rows", "series"]) {
          const v = data[key];
          if (Array.isArray(v) && v.length > 2) {
            data[key] = [...v.slice(0, 2), `… ${v.length - 2} more`];
          }
        }
        return { ...o, data };
      }),
    ),
  };
}

export function ContractJson({
  page,
  testId,
  defaultOpen = false,
}: {
  page: Page;
  testId?: string;
  defaultOpen?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const display = useMemo(() => JSON.stringify(displayPage(page), null, 2), [page]);

  const copy = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    await navigator.clipboard.writeText(JSON.stringify(page, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <details className="contract-json" data-testid={testId} open={defaultOpen}>
      <summary>
        Contract JSON — what Data-Agent sends the frontend to render this page
        <button className="chip contract-copy" onClick={copy}>
          {copied ? "copied ✓" : "copy full JSON"}
        </button>
      </summary>
      <pre data-testid={testId ? `${testId}-body` : undefined}>{display}</pre>
    </details>
  );
}
