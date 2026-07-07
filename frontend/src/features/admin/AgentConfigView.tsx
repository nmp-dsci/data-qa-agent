// Agent-Config: the published composition registry — page layouts + charts the
// agent can compose with, demo-seeded from the Hornsby worked example
// (app.agent_config, migration 0014). Each row is clickable; selecting one
// renders a live demo visualisation in the preview panel via the same report
// engine the agent's answers use.
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AgentConfigEntry, getAdminAgentConfig } from "../../lib/api";
import { AgentConfigDemoPreview } from "./AgentConfigDemo";

type Selection = { kind: "template" | "chart"; name: string };

function demoText(demo: Record<string, unknown>): string {
  const parts: string[] = [];
  if (typeof demo["question"] === "string") parts.push(`“${demo["question"]}”`);
  if (typeof demo["example"] === "string") parts.push(String(demo["example"]));
  return parts.join(" → ") || "—";
}

function DemoPanel({ entry, kind }: { entry: AgentConfigEntry; kind: Selection["kind"] }) {
  return (
    <div className="agent-demo-panel">
      <div className="agent-demo-head">
        <span className={`badge ${kind === "chart" ? "deepseek" : "stub"}`}>{kind}</span>
        <strong>{entry.title}</strong>
        <code>{entry.name}</code>
        <span className="muted agent-demo-caption">{demoText(entry.demo)}</span>
      </div>
      <div className="agent-demo-canvas report">
        <AgentConfigDemoPreview entry={entry} kind={kind} />
      </div>
    </div>
  );
}

export function AgentConfigView() {
  const q = useQuery({ queryKey: ["admin", "agent-config"], queryFn: getAdminAgentConfig });
  const [selected, setSelected] = useState<Selection | null>(null);

  const data = q.data;
  // Default the preview to the first template (the richest demo) once loaded.
  const selection: Selection | null = useMemo(() => {
    if (!data) return null;
    if (selected) return selected;
    if (data.templates[0]) return { kind: "template", name: data.templates[0].name };
    if (data.charts[0]) return { kind: "chart", name: data.charts[0].name };
    return null;
  }, [data, selected]);

  if (q.isLoading) return <p className="muted">Loading agent config...</p>;
  if (q.error) return <p className="error">{(q.error as Error).message}</p>;
  if (!data) return null;

  const selectedEntry =
    selection &&
    (selection.kind === "template" ? data.templates : data.charts).find(
      (e) => e.name === selection.name,
    );
  const isSel = (kind: Selection["kind"], name: string) =>
    selection?.kind === kind && selection.name === name;

  return (
    <>
      {selectedEntry && selection && (
        <section>
          <h3>Demo preview</h3>
          <p className="muted">
            A live render of the selected template/chart with sample data — exactly what the agent
            composes into an answer. Click any row below to preview it.
          </p>
          <DemoPanel entry={selectedEntry} kind={selection.kind} />
        </section>
      )}

      <section>
        <h3>Page layouts available</h3>
        <p className="muted">
          The template registry: the agent picks a template id per page and fills its regions — it
          cannot invent layout. New template = new frontend layout + a row here.
        </p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Template</th>
                <th>Title</th>
                <th>Regions</th>
                <th>Layout</th>
                <th>Purpose</th>
                <th>Demo</th>
              </tr>
            </thead>
            <tbody>
              {data.templates.map((t) => (
                <tr
                  key={t.name}
                  className={`selectable-row${isSel("template", t.name) ? " active" : ""}`}
                  onClick={() => setSelected({ kind: "template", name: t.name })}
                >
                  <td>
                    <code>{t.name}</code>
                  </td>
                  <td>{t.title}</td>
                  <td>{((t.spec["regions"] as string[]) ?? []).join(" · ")}</td>
                  <td>{String(t.spec["layout"] ?? "one-col")}</td>
                  <td className="wide-cell">{t.description}</td>
                  <td className="wide-cell">{demoText(t.demo)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h3>Charts available (visx)</h3>
        <p className="muted">
          The chart functions the frontend renders from an object's <code>data.intent</code> — the
          agent emits data + intent, never chart specs.
        </p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Chart fn</th>
                <th>Intent</th>
                <th>Object type</th>
                <th>Purpose</th>
                <th>Demo</th>
              </tr>
            </thead>
            <tbody>
              {data.charts.map((c) => (
                <tr
                  key={c.name}
                  className={`selectable-row${isSel("chart", c.name) ? " active" : ""}`}
                  onClick={() => setSelected({ kind: "chart", name: c.name })}
                >
                  <td>
                    <code>{c.name}</code>
                  </td>
                  <td>
                    <code>{String(c.spec["intent"] ?? "")}</code>
                  </td>
                  <td>{String(c.spec["object_type"] ?? "")}</td>
                  <td className="wide-cell">{c.description}</td>
                  <td className="wide-cell">{demoText(c.demo)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
