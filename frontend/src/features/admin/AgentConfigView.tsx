// Agent-Config: the published composition registry — page layouts + charts the
// agent can compose with, demo-seeded from the Hornsby worked example
// (app.agent_config, migration 0014).
import { useQuery } from "@tanstack/react-query";
import { getAdminAgentConfig } from "../../lib/api";

function demoText(demo: Record<string, unknown>): string {
  const parts: string[] = [];
  if (typeof demo["question"] === "string") parts.push(`“${demo["question"]}”`);
  if (typeof demo["example"] === "string") parts.push(String(demo["example"]));
  return parts.join(" → ") || "—";
}

export function AgentConfigView() {
  const q = useQuery({ queryKey: ["admin", "agent-config"], queryFn: getAdminAgentConfig });

  if (q.isLoading) return <p className="muted">Loading agent config...</p>;
  if (q.error) return <p className="error">{(q.error as Error).message}</p>;
  const data = q.data;
  if (!data) return null;

  return (
    <>
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
                <tr key={t.name}>
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
                <tr key={c.name}>
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
