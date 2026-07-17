// DataTable — the shared tabular object for the report engine and Explore. Two
// variants beyond the plain grid: `comparison` colours Target (gold) / Comparison
// (blue) columns and signs the delta; `ranked` draws an inline delta bar per row.
// Registered as the report-engine `table` object type, so Chat reports, Goldens
// and the SQL editor can render tables through the same component Explore uses.
import { chartTheme } from "./tokens";

export type CellFormat = "currency" | "number" | "percent" | "text";
export type ColumnTone = "target" | "comparison" | "delta" | null;

export interface TableColumn {
  key: string;
  label: string;
  align?: "left" | "right";
  tone?: ColumnTone;
  format?: CellFormat;
}

export interface TableData {
  title?: string | null;
  variant?: "plain" | "comparison" | "ranked";
  columns: TableColumn[];
  rows: Record<string, unknown>[];
  /** ranked variant: the numeric column whose |value| sizes the inline bar. */
  bar_key?: string | null;
}

export function formatCell(value: unknown, format: CellFormat | undefined): string {
  if (value == null || value === "") return "—";
  if (format === "text" || typeof value === "string") return String(value);
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  const abs = Math.abs(n);
  const sign = n < 0 ? "−" : "";
  const compact =
    abs >= 1_000_000
      ? `${(abs / 1_000_000).toFixed(2)}M`
      : abs >= 10_000
        ? `${Math.round(abs / 1000)}k`
        : abs.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (format === "currency") return `${sign}$${compact}`;
  if (format === "percent") return `${sign}${abs.toFixed(2)}%`;
  return `${sign}${compact}`;
}

function toneStyle(tone: ColumnTone, value: unknown): React.CSSProperties {
  if (tone === "target") return { color: "var(--cohort-target, #d9a84e)" };
  if (tone === "comparison") return { color: "var(--cohort-comparison, #7dcfff)" };
  if (tone === "delta") {
    const n = Number(value);
    if (Number.isFinite(n) && n !== 0) {
      return { color: n > 0 ? "var(--good, #9ece6a)" : "var(--bad, #f2777a)" };
    }
  }
  return {};
}

export function DataTable({ data }: { data: TableData }) {
  const theme = chartTheme();
  const variant = data.variant ?? "plain";
  const barMax =
    variant === "ranked" && data.bar_key
      ? Math.max(1, ...data.rows.map((r) => Math.abs(Number(r[data.bar_key as string]) || 0)))
      : 1;

  return (
    <div className="dt-wrap">
      {data.title && <div className="chart-title">{data.title}</div>}
      <div className="dt-scroll">
        <table className="dt" data-variant={variant}>
          <thead>
            <tr>
              {data.columns.map((c) => (
                <th
                  key={c.key}
                  style={{ textAlign: c.align ?? "left", ...toneStyle(c.tone ?? null, null) }}
                >
                  {c.label}
                </th>
              ))}
              {variant === "ranked" && data.bar_key && <th className="dt-bar-col" />}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row, i) => (
              <tr key={i}>
                {data.columns.map((c) => {
                  const v = row[c.key];
                  return (
                    <td
                      key={c.key}
                      style={{ textAlign: c.align ?? "left", ...toneStyle(c.tone ?? null, v) }}
                      className={c.format && c.format !== "text" ? "dt-num" : undefined}
                    >
                      {formatCell(v, c.format)}
                    </td>
                  );
                })}
                {variant === "ranked" && data.bar_key && (
                  <td className="dt-bar-col">
                    <DeltaBar
                      value={Number(row[data.bar_key]) || 0}
                      max={barMax}
                      good={theme.good}
                      bad={theme.bad}
                    />
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DeltaBar({
  value,
  max,
  good,
  bad,
}: {
  value: number;
  max: number;
  good: string;
  bad: string;
}) {
  const pct = Math.min(100, (Math.abs(value) / max) * 100);
  return (
    <div className="dt-bar-track" aria-hidden="true">
      <div
        className="dt-bar-fill"
        style={{ width: `${pct}%`, background: value >= 0 ? good : bad }}
      />
    </div>
  );
}
