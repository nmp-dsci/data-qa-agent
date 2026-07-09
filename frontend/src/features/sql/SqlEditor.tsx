import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { EditorView, basicSetup } from "codemirror";
import { Compartment, Prec } from "@codemirror/state";
import { keymap } from "@codemirror/view";
import { PostgreSQL, sql } from "@codemirror/lang-sql";
import { oneDark } from "@codemirror/theme-one-dark";
import {
  getCatalog,
  getSqlHistory,
  runSql,
  runSqlAi,
  track,
  type AiAction,
  type CatalogTable,
  type SqlHistoryItem,
  type SqlRunResult,
  type User,
} from "../../lib/api";
import { SpecChart } from "../../ui/SpecChart";

const SAMPLE_SQL = `-- Read-only · RLS-scoped · audited. Cmd/Ctrl+Enter to run.
-- Additive rule: derive averages from sum(total)/sum(count), never avg(avg).
SELECT suburb,
       round(sum(total_sale_value) FILTER (WHERE month >= '2024-01-01')
           / nullif(sum(n_sold) FILTER (WHERE month >= '2024-01-01'), 0)) AS avg_price_2024_on
FROM marts.property_sales
WHERE property_type = 'house'
GROUP BY suburb
HAVING sum(n_sold) >= 50
ORDER BY avg_price_2024_on DESC
LIMIT 10;`;

const TABS_KEY = "sqled.tabs.v1";
const USER_VISIBLE_SCHEMAS = new Set(["marts", "staging"]);
// dbt docs (lineage + model docs) — served by the pipeline-docs job locally.
const DBT_DOCS_URL = (import.meta.env.VITE_DBT_DOCS_URL as string) ?? "http://localhost:8180";

/** Governed profiling queries — run through the same read-only /sql executor. */
function profileTableSql(schema: string, table: string): string {
  return `-- Profile ${schema}.${table}\nSELECT count(*) AS row_count\nFROM ${schema}.${table};`;
}

function profileColumnSql(schema: string, table: string, column: string): string {
  return (
    `-- Profile ${schema}.${table}.${column}\n` +
    `SELECT count(*)                    AS rows,\n` +
    `       count(${column})           AS non_null,\n` +
    `       count(DISTINCT ${column})  AS distinct_values,\n` +
    `       min(${column})             AS min_value,\n` +
    `       max(${column})             AS max_value\n` +
    `FROM ${schema}.${table};`
  );
}

/** Filter the catalog by a search term over table + column names. */
function searchCatalog(tables: CatalogTable[], term: string): CatalogTable[] {
  const t = term.trim().toLowerCase();
  if (!t) return tables;
  return tables
    .map((table) => {
      const tableHit = `${table.schema}.${table.table}`.toLowerCase().includes(t);
      const cols = table.columns.filter((c) => c.name.toLowerCase().includes(t));
      if (tableHit) return table;
      if (cols.length > 0) return { ...table, columns: cols };
      return null;
    })
    .filter((x): x is CatalogTable => x !== null);
}

interface Draft {
  id: string;
  name: string;
  sql: string;
}

interface TabResult {
  result: SqlRunResult | null;
  error: string | null;
}

function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

function loadTabs(): Draft[] {
  try {
    const raw = localStorage.getItem(TABS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Draft[];
      if (Array.isArray(parsed) && parsed.length > 0) return parsed;
    }
  } catch {
    /* ignore malformed storage */
  }
  return [{ id: newId(), name: "query1.sql", sql: SAMPLE_SQL }];
}

function buildSchema(tables: CatalogTable[]): Record<string, string[]> {
  const schema: Record<string, string[]> = {};
  for (const t of tables) {
    schema[`${t.schema}.${t.table}`] = t.columns.map((c) => c.name);
  }
  return schema;
}

function groupCatalog(tables: CatalogTable[]): Record<string, CatalogTable[]> {
  return tables.reduce<Record<string, CatalogTable[]>>((groups, table) => {
    groups[table.schema] ??= [];
    groups[table.schema].push(table);
    return groups;
  }, {});
}

function catalogForUser(tables: CatalogTable[], user: User): CatalogTable[] {
  if (user.role === "admin") return tables;
  return tables.filter((table) => USER_VISIBLE_SCHEMAS.has(table.schema));
}

function isNumeric(v: unknown): boolean {
  return typeof v === "number" || (typeof v === "string" && v !== "" && !Number.isNaN(Number(v)));
}

/** Column index that looks numeric across the sampled rows (for chart y / sort). */
function numericColumns(rows: unknown[][], colCount: number): boolean[] {
  const flags = new Array(colCount).fill(true);
  const sample = rows.slice(0, 50);
  for (let c = 0; c < colCount; c++) {
    let seen = false;
    for (const row of sample) {
      const cell = row[c];
      if (cell === null || cell === undefined) continue;
      seen = true;
      if (!isNumeric(cell)) {
        flags[c] = false;
        break;
      }
    }
    if (!seen) flags[c] = false;
  }
  return flags;
}

function toCsv(columns: string[], rows: unknown[][]): string {
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [columns.map(esc).join(",")];
  for (const row of rows) lines.push(row.map(esc).join(","));
  return lines.join("\n");
}

function download(filename: string, content: string, type = "text/csv") {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

const CHART_MARKS = ["line", "bar", "point", "area"] as const;

type ChartMark = (typeof CHART_MARKS)[number];

interface ChartConfig {
  mark: ChartMark;
  x: string;
  y: string;
  series: string | null;
}

function isDateLike(v: unknown): boolean {
  if (v instanceof Date) return true;
  if (typeof v !== "string") return false;
  if (!/^\d{4}-\d{2}-\d{2}/.test(v)) return false;
  return !Number.isNaN(Date.parse(v));
}

function dateColumns(rows: unknown[][], colCount: number): boolean[] {
  const flags = new Array(colCount).fill(true);
  const sample = rows.slice(0, 50);
  for (let c = 0; c < colCount; c++) {
    let seen = false;
    for (const row of sample) {
      const cell = row[c];
      if (cell === null || cell === undefined || cell === "") continue;
      seen = true;
      if (!isDateLike(cell)) {
        flags[c] = false;
        break;
      }
    }
    if (!seen) flags[c] = false;
  }
  return flags;
}

function columnType(
  idx: number,
  numeric: boolean[],
  dates: boolean[],
): "quantitative" | "temporal" | "nominal" {
  if (dates[idx]) return "temporal";
  if (numeric[idx]) return "quantitative";
  return "nominal";
}

function metricScore(name: string): number {
  const n = name.toLowerCase();
  if (n === "median_price" || n === "sale_price") return 100;
  if (n.includes("median") && (n.includes("price") || n.includes("rent"))) return 95;
  if (n.includes("avg") && (n.includes("price") || n.includes("rent"))) return 90;
  if (n.includes("price") || n.includes("rent")) return 85;
  if (n.includes("growth") || n.includes("yield")) return 80;
  if (n.includes("total_sale_value")) return 75;
  if (n.includes("value")) return 70;
  return 10;
}

function isCurrencyField(name: string): boolean {
  const n = name.toLowerCase();
  return n.includes("price") || n.includes("rent") || n.includes("value");
}

function defaultChartConfig(result: SqlRunResult): ChartConfig | null {
  const { columns, rows } = result;
  if (columns.length < 2 || rows.length === 0) return null;
  const numeric = numericColumns(rows, columns.length);
  const dates = dateColumns(rows, columns.length);
  const numericIndexes = columns.map((_, idx) => idx).filter((idx) => numeric[idx]);
  if (numericIndexes.length === 0) return null;

  const yIdx = numericIndexes.reduce((best, idx) =>
    metricScore(columns[idx]) > metricScore(columns[best]) ? idx : best,
  );
  let xIdx = dates.findIndex((isDate, idx) => isDate && idx !== yIdx);
  if (xIdx === -1) xIdx = columns.findIndex((_, idx) => idx !== yIdx && !numeric[idx]);
  const resolvedXIdx = xIdx >= 0 ? xIdx : columns.findIndex((_, idx) => idx !== yIdx);
  if (resolvedXIdx === -1) return null;

  const seriesIdx = columns.findIndex((_, idx) => idx !== resolvedXIdx && idx !== yIdx && !numeric[idx]);
  return {
    mark: dates[resolvedXIdx] ? "line" : "bar",
    x: columns[resolvedXIdx],
    y: columns[yIdx],
    series: seriesIdx >= 0 ? columns[seriesIdx] : null,
  };
}

function chartValues(result: SqlRunResult): Record<string, unknown>[] {
  return result.rows.slice(0, 2000).map((row) => {
    const out: Record<string, unknown> = {};
    result.columns.forEach((col, idx) => {
      out[col] = isNumeric(row[idx]) ? Number(row[idx]) : row[idx];
    });
    return out;
  });
}

function buildChartSpec(result: SqlRunResult, config: ChartConfig): Record<string, unknown> | null {
  const { columns, rows } = result;
  if (columns.length < 2 || rows.length === 0) return null;
  const numeric = numericColumns(rows, columns.length);
  const dates = dateColumns(rows, columns.length);
  const xIdx = columns.indexOf(config.x);
  const yIdx = columns.indexOf(config.y);
  if (xIdx === -1 || yIdx === -1 || !numeric[yIdx]) return null;
  const xType = columnType(xIdx, numeric, dates);
  const hasSeries = !!config.series && columns.includes(config.series);
  const yAxis = isCurrencyField(config.y) ? { title: config.y, format: "$,.0f" } : { title: config.y };
  const mark: Record<string, unknown> = { type: config.mark };
  if (config.mark === "line") mark.point = { filled: true, size: 28 };
  if (!hasSeries) mark.color = "#b48a3f";
  const encoding: Record<string, unknown> = {
    x: {
      field: config.x,
      type: xType,
      axis: { title: config.x, labelAngle: xType === "nominal" ? -35 : 0 },
      ...(config.mark === "bar" && xType === "nominal" ? { sort: "-y" } : {}),
    },
    y: { field: config.y, type: "quantitative", axis: yAxis },
    tooltip: columns.map((field) => ({
      field,
      type: columnType(columns.indexOf(field), numeric, dates),
      ...(isCurrencyField(field) ? { format: "$,.0f" } : {}),
    })),
  };
  if (hasSeries) {
    encoding.color = { field: config.series, type: "nominal", title: config.series };
  }
  if (config.mark === "line" || config.mark === "area") {
    encoding.order = { field: config.x, type: xType };
  }
  return {
    $schema: "https://vega.github.io/schema/vega-lite/v5.json",
    width: "container",
    height: 320,
    mark,
    encoding,
    data: { values: chartValues(result) },
    background: "transparent",
  };
}

export function SqlEditor({
  user,
  onSendToChat,
  seedSql,
}: {
  user: User;
  onSendToChat?: (sql: string) => void;
  seedSql?: { sql: string; nonce: number } | null;
}) {
  const editorRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const langCompartment = useRef(new Compartment());
  const runRef = useRef<() => void>(() => {});
  const activeIdRef = useRef<string>("");

  const [tabs, setTabs] = useState<Draft[]>(loadTabs);
  const [activeId, setActiveId] = useState<string>(() => tabs[0].id);

  const [catalog, setCatalog] = useState<CatalogTable[]>([]);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [results, setResults] = useState<Record<string, TabResult>>({});
  const [running, setRunning] = useState(false);

  const [viewMode, setViewMode] = useState<"grid" | "chart">("grid");
  const [sort, setSort] = useState<{ col: number; dir: "asc" | "desc" } | null>(null);
  const [filter, setFilter] = useState("");
  const [catalogSearch, setCatalogSearch] = useState("");

  const [history, setHistory] = useState<SqlHistoryItem[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);

  const [aiPrompt, setAiPrompt] = useState("");
  const [aiBusy, setAiBusy] = useState<AiAction | null>(null);
  const [aiNote, setAiNote] = useState<string | null>(null);

  activeIdRef.current = activeId;
  const active = results[activeId] ?? { result: null, error: null };
  const sidebarCatalog = useMemo(() => catalogForUser(catalog, user), [catalog, user]);
  const visibleCatalog = useMemo(
    () => searchCatalog(sidebarCatalog, catalogSearch),
    [sidebarCatalog, catalogSearch],
  );

  // Persist drafts.
  useEffect(() => {
    try {
      localStorage.setItem(TABS_KEY, JSON.stringify(tabs));
    } catch {
      /* ignore quota errors */
    }
  }, [tabs]);

  const setEditorDoc = useCallback((text: string) => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } });
  }, []);

  // Open a new tab whenever chat sends a query over ("Open in SQL editor").
  const seedNonceRef = useRef<number>(0);
  useEffect(() => {
    if (!seedSql || seedSql.nonce === seedNonceRef.current) return;
    seedNonceRef.current = seedSql.nonce;
    addTab(seedSql.sql);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedSql]);

  // Mount the editor once.
  useEffect(() => {
    if (!editorRef.current) return;
    const view = new EditorView({
      doc: tabs[0].sql,
      parent: editorRef.current,
      extensions: [
        basicSetup,
        langCompartment.current.of(sql({ dialect: PostgreSQL })),
        oneDark,
        Prec.highest(
          keymap.of([
            {
              key: "Mod-Enter",
              preventDefault: true,
              run: () => {
                runRef.current();
                return true;
              },
            },
          ]),
        ),
        EditorView.updateListener.of((u) => {
          if (!u.docChanged) return;
          const text = u.state.doc.toString();
          const id = activeIdRef.current;
          setTabs((prev) => prev.map((t) => (t.id === id ? { ...t, sql: text } : t)));
        }),
        EditorView.theme({
          "&": { fontSize: "12.5px", backgroundColor: "#0b0d11" },
          ".cm-gutters": { backgroundColor: "#0b0d11", borderRight: "1px solid #2a2f3a" },
          "&.cm-focused": { outline: "none" },
        }),
        EditorView.lineWrapping,
      ],
    });
    viewRef.current = view;
    track("sql_editor_view");
    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Load the catalog for the browser + schema-aware autocomplete.
  useEffect(() => {
    getCatalog()
      .then(setCatalog)
      .catch(() => {});
  }, []);

  // Reconfigure SQL completion once the schema is known.
  useEffect(() => {
    if (!viewRef.current || sidebarCatalog.length === 0) return;
    viewRef.current.dispatch({
      effects: langCompartment.current.reconfigure(
        sql({ dialect: PostgreSQL, schema: buildSchema(sidebarCatalog) }),
      ),
    });
  }, [sidebarCatalog]);

  const currentSql = useCallback(
    () => viewRef.current?.state.doc.toString().trim() ?? "",
    [],
  );

  const run = useCallback(async () => {
    const text = currentSql();
    if (!text || running) return;
    const id = activeIdRef.current;
    setRunning(true);
    setSort(null);
    setFilter("");
    track("sql_query_submitted");
    try {
      const res = await runSql(text);
      setResults((r) => ({ ...r, [id]: { result: res, error: res.error ?? null } }));
      track(res.error ? "sql_query_failed" : "sql_query_succeeded", {
        row_count: res.row_count,
        latency_ms: res.latency_ms,
      });
    } catch (e) {
      const message = (e as Error).message;
      setResults((r) => ({ ...r, [id]: { result: null, error: message } }));
      track("sql_query_failed", { error: message });
    } finally {
      setRunning(false);
    }
  }, [currentSql, running]);
  runRef.current = () => void run();

  function switchTab(id: string) {
    if (id === activeIdRef.current) return;
    const tab = tabs.find((t) => t.id === id);
    if (!tab) return;
    activeIdRef.current = id;
    setActiveId(id);
    setEditorDoc(tab.sql);
    setSort(null);
    setFilter("");
  }

  function addTab(sqlText = "-- New query\n") {
    const tab: Draft = { id: newId(), name: `query${tabs.length + 1}.sql`, sql: sqlText };
    setTabs((prev) => [...prev, tab]);
    activeIdRef.current = tab.id;
    setActiveId(tab.id);
    setEditorDoc(sqlText);
    setSort(null);
    setFilter("");
    return tab.id;
  }

  function closeTab(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    if (tabs.length === 1) return;
    const idx = tabs.findIndex((t) => t.id === id);
    const next = tabs.filter((t) => t.id !== id);
    setTabs(next);
    setResults((r) => {
      const { [id]: _drop, ...rest } = r;
      return rest;
    });
    if (id === activeId) {
      const fallback = next[Math.max(0, idx - 1)];
      activeIdRef.current = fallback.id;
      setActiveId(fallback.id);
      setEditorDoc(fallback.sql);
    }
  }

  async function loadHistory() {
    const open = !historyOpen;
    setHistoryOpen(open);
    if (open) {
      try {
        setHistory(await getSqlHistory(20));
      } catch {
        setHistory([]);
      }
    }
  }

  function reloadFromHistory(item: SqlHistoryItem) {
    if (!item.sql_text) return;
    addTab(item.sql_text);
    setHistoryOpen(false);
  }

  async function askAi(action: AiAction) {
    if (aiBusy) return;
    if (action === "generate" && !aiPrompt.trim()) return;
    setAiBusy(action);
    setAiNote(null);
    track("sql_ai_requested", { action });
    try {
      const res = await runSqlAi(action, {
        prompt: aiPrompt.trim() || undefined,
        sql: action === "generate" ? undefined : currentSql(),
      });
      if (res.error) {
        setAiNote(res.error);
        return;
      }
      setAiNote(res.explanation ?? null);
      if (res.sql && action !== "explain") {
        setEditorDoc(res.sql);
        if (action === "generate") {
          setAiPrompt("");
          await run(); // insert + auto-run (per the chosen behaviour)
        }
      }
    } catch (e) {
      setAiNote((e as Error).message);
    } finally {
      setAiBusy(null);
    }
  }

  return (
    <div className="sqled">
      <aside className="schema-panel">
        <div className="schema-head">
          <div className="schema-title">Data catalog</div>
          <a
            className="schema-docs"
            href={DBT_DOCS_URL}
            target="_blank"
            rel="noreferrer"
            title="dbt docs — model documentation + lineage graph"
          >
            lineage ↗
          </a>
        </div>
        <input
          className="catalog-search"
          value={catalogSearch}
          placeholder="Search tables & columns…"
          onChange={(e) => setCatalogSearch(e.target.value)}
        />
        {sidebarCatalog.length === 0 && <div className="muted sqled-hint">Loading schema…</div>}
        {sidebarCatalog.length > 0 && visibleCatalog.length === 0 && (
          <div className="muted sqled-hint">No matches.</div>
        )}
        {Object.entries(groupCatalog(visibleCatalog)).map(([schemaName, tables]) => {
          const schemaKey = `schema:${schemaName}`;
          const schemaOpen = expanded[schemaKey] || catalogSearch.trim() !== "";
          return (
            <div className="schema-node" key={schemaName}>
              <div
                className="schema-row"
                onClick={() => setExpanded((e) => ({ ...e, [schemaKey]: !e[schemaKey] }))}
              >
                <span className="ico">{schemaOpen ? "▾" : "▸"}</span>
                <span className="schema-name">{schemaName}</span>
                <span className="schema-count">{tables.length}</span>
              </div>
              {schemaOpen && (
                <div className="schema-children">
                  {tables.map((t) => {
                    const key = `${t.schema}.${t.table}`;
                    const open = expanded[key] || catalogSearch.trim() !== "";
                    return (
                      <div className="tbl" key={key}>
                        <div
                          className="tname"
                          onClick={() => setExpanded((e) => ({ ...e, [key]: !e[key] }))}
                        >
                          <span className="ico">{open ? "▾" : "▸"}</span>
                          <span
                            className="tb"
                            title={t.description ?? ""}
                            onClick={(ev) => {
                              ev.stopPropagation();
                              insertAtCursor(viewRef.current, key);
                            }}
                          >
                            {t.table}
                          </span>
                        </div>
                        {open && (
                          <>
                            <div className="tbl-actions">
                              <button
                                className="chip"
                                title={`Row count for ${key} (governed, read-only)`}
                                onClick={() => {
                                  addTab(profileTableSql(t.schema, t.table));
                                  void run();
                                }}
                              >
                                profile
                              </button>
                              {onSendToChat && (
                                <button
                                  className="chip"
                                  title="Ask the data agent about this table"
                                  onClick={() =>
                                    onSendToChat(
                                      `Tell me about ${key} — what does it contain and what insights can you produce from it?`,
                                    )
                                  }
                                >
                                  ask agent
                                </button>
                              )}
                            </div>
                            <ul className="cols">
                              {t.columns.map((c) => (
                                <li
                                  key={c.name}
                                  onClick={() => insertAtCursor(viewRef.current, c.name)}
                                  title={c.description ?? ""}
                                >
                                  <span>{c.name}</span>
                                  <span className="col-side">
                                    <span className="ctype">{c.type ?? ""}</span>
                                    <button
                                      className="col-profile"
                                      title={`Profile ${c.name}: nulls, distinct, min/max`}
                                      onClick={(ev) => {
                                        ev.stopPropagation();
                                        addTab(profileColumnSql(t.schema, t.table, c.name));
                                        void run();
                                      }}
                                    >
                                      Σ
                                    </button>
                                  </span>
                                </li>
                              ))}
                            </ul>
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </aside>

      <div className="sqled-work">
        {/* Draft tabs */}
        <div className="qtabs">
          {tabs.map((t) => (
            <span
              key={t.id}
              className={t.id === activeId ? "qtab active" : "qtab"}
              onClick={() => switchTab(t.id)}
              title={t.name}
            >
              {t.name}
              {tabs.length > 1 && (
                <button className="qtab-close" onClick={(e) => closeTab(t.id, e)} title="Close">
                  ×
                </button>
              )}
            </span>
          ))}
          <button className="qtab add" onClick={() => addTab()} title="New query">
            +
          </button>
        </div>

        {/* AI assist bar */}
        <div className="ai-bar">
          <span className="ai-spark">✨</span>
          <input
            className="ai-input"
            value={aiPrompt}
            placeholder="Ask AI to write SQL — e.g. top 10 suburbs by rent growth"
            onChange={(e) => setAiPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                askAi("generate");
              }
            }}
          />
          <button className="btn-ai" onClick={() => askAi("generate")} disabled={!!aiBusy}>
            {aiBusy === "generate" ? "…" : "Ask AI"}
          </button>
          <button className="btn-ghost" onClick={() => askAi("explain")} disabled={!!aiBusy}>
            {aiBusy === "explain" ? "…" : "Explain"}
          </button>
          <button className="btn-ghost" onClick={() => askAi("fix")} disabled={!!aiBusy}>
            {aiBusy === "fix" ? "…" : "Fix"}
          </button>
          <button className="btn-ghost" onClick={() => askAi("optimize")} disabled={!!aiBusy}>
            {aiBusy === "optimize" ? "…" : "Optimize"}
          </button>
        </div>

        <div className="sqled-bar">
          <span className="kbd-hint">Read-only · RLS-scoped · Cmd/Ctrl+Enter to run</span>
          <div className="run-actions">
            <button className="btn-ghost" onClick={loadHistory}>
              History
            </button>
            {onSendToChat && (
              <button className="btn-ghost" onClick={() => onSendToChat(currentSql())}>
                Send to chat
              </button>
            )}
            <button className="btn-run" onClick={() => void run()} disabled={running}>
              {running ? "Running…" : "▶ Run"}
            </button>
          </div>
        </div>

        {aiNote && (
          <div className="ai-note">
            <span className="ai-spark">✨</span> {aiNote}
          </div>
        )}

        {historyOpen && (
          <div className="history-panel">
            <div className="history-title">Recent runs</div>
            {history.length === 0 && <div className="muted sqled-hint">No history yet.</div>}
            {history.map((h) => (
              <div key={h.id} className="history-row" onClick={() => reloadFromHistory(h)}>
                <span className={`badge src-sql_editor status-${h.status}`}>{h.status}</span>
                <span className="history-sql">{(h.sql_text ?? "").replace(/\s+/g, " ").slice(0, 80)}</span>
                <span className="history-meta">
                  {h.row_count} rows · {h.latency_ms ?? "-"} ms
                </span>
              </div>
            ))}
          </div>
        )}

        <div className="sqled-editor" ref={editorRef} />

        {active.error && <div className="error sqled-error">{active.error}</div>}
        {active.result && !active.result.error && (
          <SqlResults
            result={active.result}
            viewMode={viewMode}
            setViewMode={setViewMode}
            sort={sort}
            setSort={setSort}
            filter={filter}
            setFilter={setFilter}
          />
        )}
      </div>
    </div>
  );
}

function insertAtCursor(view: EditorView | null, text: string) {
  if (!view) return;
  const { from, to } = view.state.selection.main;
  // Prepend a space when the cursor abuts a preceding token, so inserting a
  // table/column name can't glue onto it (e.g. "...eval_cases" + "marts.x" →
  // "...eval_casesmarts.x", which then fails to parse). A separator (whitespace,
  // '.', or '(') before the cursor means no extra space is needed.
  const before = from > 0 ? view.state.doc.sliceString(from - 1, from) : "";
  const insert = before && !/[\s.(]/.test(before) ? ` ${text}` : text;
  view.dispatch({
    changes: { from, to, insert },
    selection: { anchor: from + insert.length },
  });
  view.focus();
}

interface ResultsProps {
  result: SqlRunResult;
  viewMode: "grid" | "chart";
  setViewMode: (m: "grid" | "chart") => void;
  sort: { col: number; dir: "asc" | "desc" } | null;
  setSort: (s: { col: number; dir: "asc" | "desc" } | null) => void;
  filter: string;
  setFilter: (f: string) => void;
}

function SqlResults({ result, viewMode, setViewMode, sort, setSort, filter, setFilter }: ResultsProps) {
  const defaultConfig = useMemo(() => defaultChartConfig(result), [result]);
  const [chartConfig, setChartConfig] = useState<ChartConfig | null>(defaultConfig);

  useEffect(() => {
    setChartConfig(defaultConfig);
  }, [defaultConfig]);

  const numeric = useMemo(
    () => numericColumns(result.rows, result.columns.length),
    [result.columns.length, result.rows],
  );
  const chartSpec = useMemo(
    () => (chartConfig ? buildChartSpec(result, chartConfig) : null),
    [chartConfig, result],
  );

  const rows = useMemo(() => {
    let out = result.rows;
    if (filter.trim()) {
      const f = filter.toLowerCase();
      out = out.filter((row) => row.some((c) => String(c ?? "").toLowerCase().includes(f)));
    }
    if (sort) {
      const { col, dir } = sort;
      out = [...out].sort((a, b) => {
        const av = a[col];
        const bv = b[col];
        if (av === bv) return 0;
        if (av === null || av === undefined) return 1;
        if (bv === null || bv === undefined) return -1;
        let cmp: number;
        if (isNumeric(av) && isNumeric(bv)) cmp = Number(av) - Number(bv);
        else cmp = String(av) < String(bv) ? -1 : 1;
        return dir === "asc" ? cmp : -cmp;
      });
    }
    return out;
  }, [result.rows, filter, sort]);

  function toggleSort(col: number) {
    if (!sort || sort.col !== col) setSort({ col, dir: "asc" });
    else if (sort.dir === "asc") setSort({ col, dir: "desc" });
    else setSort(null);
  }

  function updateChartConfig(patch: Partial<ChartConfig>) {
    setChartConfig((cfg) => (cfg ? { ...cfg, ...patch } : cfg));
  }

  return (
    <div className="result sqled-result">
      <div className="meta">
        <span className="badge sql_editor">sql editor</span>
        <span>{result.row_count} rows</span>
        {result.latency_ms != null && <span>{result.latency_ms} ms</span>}
        {result.truncated && (
          <span className="trunc">· showing first {result.row_count} (row cap hit)</span>
        )}
        <div className="results-actions">
          <button
            className={viewMode === "grid" ? "chip active" : "chip"}
            onClick={() => setViewMode("grid")}
          >
            Grid
          </button>
          <button
            className={viewMode === "chart" ? "chip active" : "chip"}
            onClick={() => setViewMode("chart")}
            disabled={!defaultConfig}
            title={defaultConfig ? "" : "Need at least one numeric column to chart"}
          >
            Chart
          </button>
          <button
            className="chip"
            onClick={() =>
              download("query_result.csv", toCsv(result.columns, result.rows))
            }
          >
            ⭳ CSV
          </button>
        </div>
      </div>

      {viewMode === "grid" && result.columns.length > 0 && (
        <>
          <input
            className="result-filter"
            value={filter}
            placeholder="Filter rows…"
            onChange={(e) => setFilter(e.target.value)}
          />
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {result.columns.map((c, ci) => (
                    <th key={c} className="sortable" onClick={() => toggleSort(ci)}>
                      {c}
                      {sort?.col === ci && <span className="sort-ind">{sort.dir === "asc" ? " ▲" : " ▼"}</span>}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 200).map((row, ri) => (
                  <tr key={ri}>
                    {row.map((cell, ci) => (
                      <td key={ci}>{cell === null ? "∅" : String(cell)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {viewMode === "chart" && chartConfig && (
        <div className="chart-builder">
          <div className="chart-controls">
            <label>
              <span>Mark</span>
              <select
                value={chartConfig.mark}
                onChange={(e) => updateChartConfig({ mark: e.target.value as ChartMark })}
              >
                {CHART_MARKS.map((mark) => (
                  <option key={mark} value={mark}>
                    {mark}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>X</span>
              <select value={chartConfig.x} onChange={(e) => updateChartConfig({ x: e.target.value })}>
                {result.columns.map((col) => (
                  <option key={col} value={col}>
                    {col}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Y</span>
              <select value={chartConfig.y} onChange={(e) => updateChartConfig({ y: e.target.value })}>
                {result.columns.map((col, idx) => (
                  <option key={col} value={col} disabled={!numeric[idx]}>
                    {col}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Series</span>
              <select
                value={chartConfig.series ?? ""}
                onChange={(e) => updateChartConfig({ series: e.target.value || null })}
              >
                <option value="">None</option>
                {result.columns
                  .filter((col) => col !== chartConfig.x && col !== chartConfig.y)
                  .map((col) => (
                    <option key={col} value={col}>
                      {col}
                    </option>
                  ))}
              </select>
            </label>
          </div>
          {chartSpec ? (
            <SpecChart spec={chartSpec} />
          ) : (
            <p className="muted sqled-hint">Select a numeric Y column to chart.</p>
          )}
        </div>
      )}

      {result.columns.length === 0 && (
        <p className="muted sqled-hint">Query ran successfully — no rows returned.</p>
      )}
    </div>
  );
}
