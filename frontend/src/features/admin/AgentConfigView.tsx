// Template Studio — the published composition registry (page templates + chart
// functions) with a live Template Preview, the contract JSON that produced it,
// and a playground for composing pages by hand. Everything renders through the
// SAME PageLayout the chat report engine uses, so what the Studio shows is
// exactly how the Data-Agent informs the frontend to build an answer page.
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AgentConfigEntry, getAdminAgentConfig } from "../../lib/api";
import { AgentConfigDemoPreview, demoPageFor } from "./AgentConfigDemo";
import { ContractJson } from "../../ui/ContractJson";
import { TemplatePlayground } from "./TemplatePlayground";

type Selection = { kind: "template" | "chart"; name: string };

function demoText(demo: Record<string, unknown>): string {
  const parts: string[] = [];
  if (typeof demo["question"] === "string") parts.push(`“${demo["question"]}”`);
  if (typeof demo["example"] === "string") parts.push(String(demo["example"]));
  return parts.join(" → ") || "—";
}

function PreviewPanel({ entry, kind }: { entry: AgentConfigEntry; kind: Selection["kind"] }) {
  const page = demoPageFor(entry, kind);
  return (
    <div className="agent-demo-panel" data-testid="template-preview">
      <div className="agent-demo-head">
        <span className={`badge ${kind === "chart" ? "deepseek" : "stub"}`}>{kind}</span>
        <strong>{entry.title}</strong>
        <code>{entry.name}</code>
        <span className="muted agent-demo-caption">{demoText(entry.demo)}</span>
      </div>
      <div className="agent-demo-canvas report" data-testid="template-preview-canvas">
        <AgentConfigDemoPreview entry={entry} kind={kind} />
      </div>
      {page && <ContractJson page={page} testId="template-preview-json" />}
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

  if (q.isLoading) return <p className="muted">Loading template studio...</p>;
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
          <h3>Template Preview</h3>
          <p className="muted">
            How the Data-Agent informs the frontend to build a page: it picks a template, fills its
            columns with typed objects (data + intent — never chart specs), and the report engine
            renders it. This preview uses the production renderer with sample data; expand the
            contract JSON to see exactly what the agent sends. Click any row below to preview it.
          </p>
          <PreviewPanel entry={selectedEntry} kind={selection.kind} />
        </section>
      )}

      <section>
        <h3>Page layouts available</h3>
        <p className="muted">
          The template registry: the agent picks a template id per page and fills its columns
          positionally (<code>columns[i][j]</code> = column i, slot j) — it cannot invent layout.
          New template = new frontend layout + a row here.
        </p>
        <div className="table-wrap">
          <table data-testid="templates-table">
            <thead>
              <tr>
                <th>Template</th>
                <th>Title</th>
                <th>Columns</th>
                <th>Purpose</th>
                <th>Demo</th>
              </tr>
            </thead>
            <tbody>
              {data.templates.map((t) => (
                <tr
                  key={t.name}
                  data-testid={`template-row-${t.name}`}
                  className={`selectable-row${isSel("template", t.name) ? " active" : ""}`}
                  onClick={() => setSelected({ kind: "template", name: t.name })}
                >
                  <td>
                    <code>{t.name}</code>
                  </td>
                  <td>{t.title}</td>
                  <td>{String(t.spec["columns"] ?? 1)}</td>
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
          agent emits data + intent, never chart specs. Any chart can go in any column.
        </p>
        <div className="table-wrap">
          <table data-testid="charts-table">
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
                  data-testid={`chart-row-${c.name}`}
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

      <TemplatePlayground />
    </>
  );
}
