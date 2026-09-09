// SimpleTable — a plain HTML table for rows of column→value records.
// Presentation-handover (s46): the in-browser chart/report renderer is gone —
// Slides/Sheets are the report now — so every surface that used to hand its
// data to the chart stack (Explore, Ops, Goldens) renders it as a plain table
// through this one shared component instead.
export interface SimpleColumn {
  key: string;
  label: string;
  align?: "left" | "right";
}

/** Row values as they'd come off a page object / aggregate result: numbers get
 *  thousands separators, null/undefined read as an em dash, everything else is
 *  stringified as-is (no currency/percent unit formatting — that lived in the
 *  deleted chart stack and this table doesn't reconstruct it). */
function formatCell(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "number") return Number.isFinite(value) ? value.toLocaleString() : String(value);
  return String(value);
}

export function SimpleTable({
  columns,
  rows,
  max,
  className,
}: {
  columns: SimpleColumn[];
  rows: Record<string, unknown>[];
  /** Cap the rendered rows (the table still reports the true row count below). */
  max?: number;
  className?: string;
}) {
  const shown = max != null ? rows.slice(0, max) : rows;
  return (
    <div className={`table-wrap${className ? ` ${className}` : ""}`}>
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} style={c.align === "right" ? { textAlign: "right" } : undefined}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c.key} style={c.align === "right" ? { textAlign: "right" } : undefined}>
                  {formatCell(row[c.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {max != null && rows.length > max && (
        <div className="muted" style={{ padding: "6px 10px", fontSize: 11 }}>
          {rows.length.toLocaleString()} rows · showing {max}
        </div>
      )}
    </div>
  );
}

/** Columns inferred from the keys of the first row — for call sites that have
 *  records but no explicit column list (e.g. a raw SQL/aggregate result). */
export function columnsFromRows(rows: Record<string, unknown>[]): SimpleColumn[] {
  const first = rows[0];
  return first ? Object.keys(first).map((key) => ({ key, label: key })) : [];
}

/** The "nothing to chart here anymore" empty state — for a spot that used to
 *  render an object with no underlying rows to fall back to as a table. */
export function ChartsMovedNote({ children }: { children?: React.ReactNode }) {
  return (
    <p className="muted ex-hint">
      {children ?? "Charting has moved to Slides — ask the agent to build a presentation for this view."}
    </p>
  );
}
