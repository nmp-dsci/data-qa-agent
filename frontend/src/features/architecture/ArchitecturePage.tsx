// The Architecture tab (M5, agent_sdk migration) — the GenAI system,
// visualised in the app itself: the pipeline as a flight route, the live
// knowledge base a real run reads, the tool registry it's allowed to call,
// and a step-through of one real run's trace (inspect_run.py as a UI). Part
// of the migration's demo surface, so it leans entirely on primitives the app
// already owns — the Flight Deck kit for the map, AgentTrace for the
// walk-through, the .config-table look for the registry — nothing bespoke.
import { ReactNode, useEffect, useMemo, useState } from "react";
import {
  AdminQueryRun,
  ArchitectureData,
  ArchitectureKnowledgeFile,
  ArchitectureRuntime,
  ArchitectureTool,
  getAdminQueryRuns,
  getArchitecture,
  getArchitectureContent,
} from "../../lib/api";
import { AgentTrace, RunId, traceSummary } from "../../ui/AgentTrace";
import { FlightPath, FlightStop, InstrumentLabel } from "../../ui/flightdeck";

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section className="arch-section">
      <div className="arch-section-label">
        <InstrumentLabel tone="dim">{label}</InstrumentLabel>
      </div>
      {children}
    </section>
  );
}

/* ---------------------------------------------------------------------------
 * System map — the pipeline as a flight route: frontend → backend-api →
 * agent runtime → MCP tools → SQL guard/RLS → Postgres → Sheets/Slides. Static (flying=false,
 * every stop lit) because this is the always-on pipeline, not a progress bar.
 * ------------------------------------------------------------------------- */

const MAP_STOPS: FlightStop[] = [
  { key: "frontend", label: "Frontend", note: "React · Slides/Sheets artifact viewer" },
  { key: "backend", label: "Backend API", note: "auth · RLS session · agent proxy" },
  { key: "runtime", label: "Agent runtime", note: "champion / challenger — see badge below" },
  { key: "tools", label: "MCP tools", note: "extract · run_analysis · lookup_values · add_slide" },
  { key: "guard", label: "SQL guard + RLS", note: "sql_guardrails · agent_ro role" },
  { key: "db", label: "Postgres", note: "marts · staging · app" },
  { key: "deck", label: "Sheets + Slides", note: "start_deck · add_slide — the only egress" },
];

function runtimeBadgeClass(runtime: ArchitectureRuntime): string {
  if (runtime.agent_runtime === "agent_sdk") return "badge claude";
  return runtime.provider === "deepseek" ? "badge deepseek" : "badge claude";
}

function RuntimeBadge({ runtime }: { runtime?: ArchitectureRuntime }) {
  if (!runtime) return <span className="muted">not available</span>;
  return (
    <span className="arch-runtime-badge">
      <span className={runtimeBadgeClass(runtime)}>{runtime.agent_runtime}</span>
      <code>{runtime.model}</code>
      {runtime.fingerprint?.fingerprint && (
        <span className="muted" title="build fingerprint (version.build_fingerprint)">
          {runtime.fingerprint.fingerprint}
        </span>
      )}
    </span>
  );
}

function SystemMap({ runtime }: { runtime?: ArchitectureRuntime }) {
  return (
    <div className="arch-map">
      <FlightPath stops={MAP_STOPS} active={MAP_STOPS.length - 1} flying={false} />
      <div className="arch-map-foot">
        <div className="arch-map-note">
          <strong>Agent runtime</strong> <RuntimeBadge runtime={runtime} />
        </div>
        <div className="arch-map-note">
          <strong>Workspace</strong>{" "}
          <span className="muted">
            CLAUDE.md · marts.md · layouts.md · schema/*.md · knowledge/* — explored with Read/Grep/Glob only
          </span>
        </div>
        <div className="arch-map-note">
          <strong>Sandbox</strong>{" "}
          <span className="muted">
            run_analysis → skills.* → frames; add_slide writes each frame to the run's Sheet
            and a LINKED chart into the deck (no browser rendering)
          </span>
        </div>
        {runtime && (
          <div className="arch-map-note">
            <strong>Quotas</strong>{" "}
            <span className="muted">
              {runtime.quotas.max_sql_attempts} SQL attempts · {runtime.quotas.sandbox_run_attempts}{" "}
              sandbox runs · {runtime.quotas.agent_request_limit} model turns ·{" "}
              {runtime.quotas.max_knowledge_reads} knowledge reads ·{" "}
              {runtime.quotas.max_slides > 0
                ? `${runtime.quotas.max_slides} slides`
                : "deck export off"}{" "}
              · sandbox={runtime.sandbox_runtime}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * Knowledge base browser — every file a real run workspace would contain,
 * without the tab ever building one (the API reads the same pure functions
 * workspace.py calls, not workspace.build_workspace()).
 * ------------------------------------------------------------------------- */

const KIND_LABEL: Record<string, string> = {
  claude_md: "workflow template",
  marts: "mart index",
  layouts: "slide layout catalogue",
  schema: "schema docs",
  knowledge: "knowledge pages",
};

function fileKey(f: ArchitectureKnowledgeFile): string {
  return `${f.kind}:${f.id}`;
}

function KnowledgeBrowser({
  files,
  knowledgeVersion,
}: {
  files: ArchitectureKnowledgeFile[];
  knowledgeVersion: string;
}) {
  const [selectedKey, setSelectedKey] = useState(() => (files[0] ? fileKey(files[0]) : ""));
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const selected = useMemo(
    () => files.find((f) => fileKey(f) === selectedKey) ?? files[0] ?? null,
    [files, selectedKey],
  );

  const groups = useMemo(() => {
    const g: Record<string, ArchitectureKnowledgeFile[]> = {};
    for (const f of files) (g[f.kind] ??= []).push(f);
    return g;
  }, [files]);

  useEffect(() => {
    if (!selected) return;
    let live = true;
    setLoading(true);
    setErr("");
    getArchitectureContent(selected.kind, selected.id)
      .then((r) => {
        if (live) setContent(r.content);
      })
      .catch((e: unknown) => {
        if (live) setErr((e as Error).message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.kind, selected?.id]);

  return (
    <div className="arch-kb">
      <div className="arch-kb-list">
        <div className="arch-kb-version muted">knowledge_version {knowledgeVersion}</div>
        {Object.entries(groups).map(([kind, items]) => (
          <div key={kind} className="arch-kb-group">
            <div className="arch-kb-group-label">{KIND_LABEL[kind] ?? kind}</div>
            {items.map((f) => (
              <button
                key={fileKey(f)}
                className={fileKey(f) === selectedKey ? "arch-kb-item active" : "arch-kb-item"}
                onClick={() => setSelectedKey(fileKey(f))}
                title={f.description}
              >
                <span className="arch-kb-item-name">{f.filename}</span>
                <span className="arch-kb-item-size muted">{f.size.toLocaleString()}b</span>
              </button>
            ))}
          </div>
        ))}
      </div>
      <div className="arch-kb-pane">
        {selected ? (
          <>
            <div className="arch-kb-pane-head">
              <strong>{selected.filename}</strong>
              {selected.sha256 && (
                <code className="muted" title="sha256 of this file's content">
                  sha256 {selected.sha256.slice(0, 12)}
                </code>
              )}
            </div>
            {selected.description && <p className="muted">{selected.description}</p>}
            {loading && <p className="muted">loading…</p>}
            {err && <p className="error">{err}</p>}
            {!loading && !err && <pre className="arch-kb-content">{content}</pre>}
          </>
        ) : (
          <p className="muted">No files.</p>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * Tools / MCP registry — generated on the backend from sdk_agent's own tool
 * definitions, so this table can't drift from what a live run actually calls.
 * ------------------------------------------------------------------------- */

function ToolsTable({ tools }: { tools: ArchitectureTool[] }) {
  return (
    <div className="config-card">
      <table className="config-table arch-tools-table">
        <thead>
          <tr>
            <th>tool</th>
            <th>kind</th>
            <th>description</th>
            <th>quota</th>
            <th>guardrail</th>
          </tr>
        </thead>
        <tbody>
          {tools.map((t) => (
            <tr key={`${t.kind}:${t.name}`}>
              <th>
                <code>{t.kind === "mcp" ? `mcp__${t.server}__${t.name}` : t.name}</code>
              </th>
              <td>
                <span className="badge">{t.kind}</span>
              </td>
              <td>{t.description}</td>
              <td className="muted">{t.quota}</td>
              <td className="muted">{t.guardrail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * Run walk-through — inspect_run.py as a UI. Reuses GET /admin/query-runs
 * (already admin-gated, already returns the full trace jsonb per run) rather
 * than a parallel endpoint — see architecture.py's module docstring.
 * ------------------------------------------------------------------------- */

function engineBadgeClass(engine: string): string {
  if (engine === "agent_sdk") return "badge claude";
  if (engine === "stub") return "badge stub";
  return "badge deepseek";
}

function RunWalkthrough() {
  const [runs, setRuns] = useState<AdminQueryRun[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    getAdminQueryRuns({ limit: 50 })
      .then((rs) => {
        const agentRuns = rs.filter((r) => r.source === "agent");
        setRuns(agentRuns);
        if (agentRuns[0]) setSelectedId(agentRuns[0].id);
      })
      .catch((e: unknown) => setErr((e as Error).message));
  }, []);

  const run = runs.find((r) => r.id === selectedId) ?? null;

  return (
    <div className="arch-runs">
      <div className="arch-runs-list">
        {err && <p className="error">{err}</p>}
        {!err && runs.length === 0 && (
          <p className="muted">No agent runs yet — ask a question in Chat, then come back.</p>
        )}
        {runs.map((r) => (
          <button
            key={r.id}
            className={r.id === selectedId ? "arch-run-item active" : "arch-run-item"}
            onClick={() => setSelectedId(r.id)}
          >
            <span className="arch-run-q">{r.question || "(no question recorded)"}</span>
            <span className={`badge status-${r.status}`}>{r.status}</span>
            <span className="muted arch-run-when">
              {r.created_at ? new Date(r.created_at).toLocaleString() : ""}
            </span>
          </button>
        ))}
      </div>
      <div className="arch-run-detail">
        {run ? (
          <>
            <div className="arch-run-head">
              <RunId id={run.id} />
              <span className={engineBadgeClass(run.engine)}>{run.engine}</span>
              {run.error && <span className="error">{run.error}</span>}
            </div>
            <AgentTrace
              steps={run.trace ?? []}
              summary={traceSummary({
                engine: run.engine,
                steps: run.trace ?? [],
                latency_ms: run.latency_ms,
                input_tokens: run.input_tokens,
                output_tokens: run.output_tokens,
              })}
            />
          </>
        ) : (
          <p className="muted">Pick a run on the left.</p>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * The tab
 * ------------------------------------------------------------------------- */

export function ArchitecturePage() {
  const [data, setData] = useState<ArchitectureData | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    getArchitecture()
      .then(setData)
      .catch((e: unknown) => setErr((e as Error).message));
  }, []);

  return (
    <main className="architecture" aria-label="Architecture">
      <header className="arch-head">
        <div>
          <InstrumentLabel tone="accent">data pilot · architecture</InstrumentLabel>
          <div className="arch-sub">
            The GenAI system, visualised — pipeline, live knowledge base, tool registry, and a real
            run's trace.
          </div>
        </div>
      </header>

      {err && <p className="error">{err}</p>}
      {data && !data.available && (
        <p className="error">{data.error ?? "data-agent unavailable"}</p>
      )}

      <Section label="system map">
        <SystemMap runtime={data?.runtime} />
      </Section>

      <Section label="knowledge base">
        {data?.knowledge ? (
          <KnowledgeBrowser
            files={data.knowledge.files}
            knowledgeVersion={data.knowledge.knowledge_version}
          />
        ) : (
          <p className="muted">Not available.</p>
        )}
      </Section>

      <Section label="tools & MCP registry">
        {data?.tools ? <ToolsTable tools={data.tools} /> : <p className="muted">Not available.</p>}
      </Section>

      <Section label="run walk-through">
        <RunWalkthrough />
      </Section>
    </main>
  );
}
