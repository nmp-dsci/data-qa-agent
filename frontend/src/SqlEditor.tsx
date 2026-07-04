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
} from "./api";
import { VegaChart } from "./VegaChart";

const SAMPLE_SQL = `-- Read-only · RLS-scoped · audited. Cmd/Ctrl+Enter to run.
SELECT suburb,
       round(max(median_price) FILTER (WHERE month >= '2024-01-01')
           / nullif(min(median_price), 0) * 100 - 100, 1) AS growth_pct
FROM marts.mart_sales_summary
WHERE property_type = 'house'
GROUP BY suburb
HAVING sum(n_sold) >= 50
ORDER BY growth_pct DESC
LIMIT 10;`;

const TABS_KEY = "sqled.tabs.v1";
const USER_VISIBLE_SCHEMAS = new Set(["marts", "staging"]);

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

/** A minimal Vega-Lite bar spec: first non-numeric column as x, first numeric as y. */
function buildChartSpec(result: SqlRunResult): Record<string, unknown> | null {
  const { columns, rows } = result;
  if (columns.length < 2 || rows.length === 0) return null;
  const numeric = numericColumns(rows, columns.length);
  const yIdx = numeric.findIndex((n) => n);
  if (yIdx === -1) return null;
  let xIdx = numeric.findIndex((n) => !n);
  if (xIdx === -1) xIdx = yIdx === 0 ? 1 : 0;
  const values = rows.slice(0, 50).map((row) => ({
    [columns[xIdx]]: row[xIdx] as string | number,
    [columns[yIdx]]: isNumeric(row[yIdx]) ? Number(row[yIdx]) : row[yIdx],
  }));
  return {
    $schema: "https://vega.github.io/schema/vega-lite/v5.json",
    width: "container",
    height: 260,
    mark: { type: "bar", color: "#b48a3f" },
    encoding: {
      x: { field: columns[xIdx], type: "nominal", sort: "-y", axis: { labelAngle: -40 } },
      y: { field: columns[yIdx], type: "quantitative" },
    },
    data: { values },
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

  const [history, setHistory] = useState<SqlHistoryItem[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);

  const [aiPrompt, setAiPrompt] = useState("");
  const [aiBusy, setAiBusy] = useState<AiAction | null>(null);
  const [aiNote, setAiNote] = useState<string | null>(null);

  activeIdRef.current = activeId;
  const active = results[activeId] ?? { result: null, error: null };
  const sidebarCatalog = useMemo(() => catalogForUser(catalog, user), [catalog, user]);

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
        <div className="schema-title">Schema</div>
        {sidebarCatalog.length === 0 && <div className="muted sqled-hint">Loading schema…</div>}
        {Object.entries(groupCatalog(sidebarCatalog)).map(([schemaName, tables]) => {
          const schemaKey = `schema:${schemaName}`;
          const schemaOpen = expanded[schemaKey];
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
                    const open = expanded[key];
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
                          <ul className="cols">
                            {t.columns.map((c) => (
                              <li
                                key={c.name}
                                onClick={() => insertAtCursor(viewRef.current, c.name)}
                                title={c.description ?? ""}
                              >
                                <span>{c.name}</span>
                                <span className="ctype">{c.type ?? ""}</span>
                              </li>
                            ))}
                          </ul>
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
  view.dispatch({
    changes: { from, to, insert: text },
    selection: { anchor: from + text.length },
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
  const chartSpec = useMemo(() => buildChartSpec(result), [result]);

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
            disabled={!chartSpec}
            title={chartSpec ? "" : "Need a text + numeric column to chart"}
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

      {viewMode === "chart" && chartSpec && <VegaChart spec={chartSpec} />}

      {result.columns.length === 0 && (
        <p className="muted sqled-hint">Query ran successfully — no rows returned.</p>
      )}
    </div>
  );
}
