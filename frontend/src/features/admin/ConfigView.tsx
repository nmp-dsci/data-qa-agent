import { AdminConfig } from "../../lib/api";

export function ConfigView({ config }: { config: AdminConfig }) {
  return (
    <section className="config">
      <h3>Configuration</h3>
      <p className="muted">
        Resolved runtime config across services. Secrets are redacted — values marked{" "}
        <span className="badge secret">secret</span> only show whether they are set.
      </p>
      <div className="config-grid">
        {config.sections.map((s) => (
          <div key={s.service} className="config-card">
            <h4>
              {s.title} <code className="config-svc">{s.service}</code>
            </h4>
            {s.error ? (
              <p className="error">{s.error}</p>
            ) : (
              <table className="config-table">
                <tbody>
                  {s.items.map((item) => (
                    <tr key={item.key}>
                      <th>{item.key}</th>
                      <td>
                        <code>{item.value}</code>
                        {item.secret && <span className="badge secret">secret</span>}
                        {item.note && <span className="config-note">{item.note}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
