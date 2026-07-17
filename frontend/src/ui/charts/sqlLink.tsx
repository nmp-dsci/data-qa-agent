// Chart -> SQL editor link. Any chart whose data carries a `sql` string shows a
// "SQL" action (next to PNG export) that opens the SQL editor tab seeded with that
// query, so a user can view and run the query that produced the chart. The opener
// is provided app-wide via context (App wires it to "switch to SQL tab + seed"),
// so charts on any surface (Explore, Chat reports, Goldens) light up automatically
// once their data includes `sql`.
import { createContext, useContext } from "react";

export const ChartSqlContext = createContext<((sql: string) => void) | null>(null);

export function ChartSqlButton({ sql }: { sql?: string | null }) {
  const openSql = useContext(ChartSqlContext);
  if (!sql || !openSql) return null;
  return (
    <button
      className="chart-export chart-sql"
      title="Open this query in the SQL editor"
      aria-label="Open query in SQL editor"
      onClick={(e) => {
        e.stopPropagation();
        openSql(sql);
      }}
    >
      SQL
    </button>
  );
}
