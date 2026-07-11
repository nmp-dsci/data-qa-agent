// Settings / Profile: theme toggle, model provider, the agent's remembered
// preferences (read/forget — owner-only under RLS), and a data-access summary.
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  deleteMyMemory,
  getAdminConfig,
  getMyAccess,
  getMyMemories,
  User,
} from "../../lib/api";
import { formatTime } from "../../lib/format";
import { setTheme, useTheme } from "../../lib/theme";

function ThemeSection() {
  const theme = useTheme();
  return (
    <section>
      <h3>Appearance</h3>
      <div className="settings-row">
        <span className="fb-sent">
          <button className={theme === "dark" ? "sel" : ""} onClick={() => setTheme("dark")}>
            Dark
          </button>
          <button className={theme === "light" ? "sel" : ""} onClick={() => setTheme("light")}>
            Light
          </button>
        </span>
        <span className="muted">Charts and every panel follow the design tokens.</span>
      </div>
    </section>
  );
}

function ModelSection({ user }: { user: User }) {
  const isAdmin = user.role === "admin";
  const q = useQuery({
    queryKey: ["admin", "config"],
    queryFn: getAdminConfig,
    enabled: isAdmin,
  });
  let provider = "managed by your administrator";
  let model: string | null = null;
  if (isAdmin && q.data) {
    const agent = q.data.sections.find((s) => s.service === "data-agent");
    provider = agent?.items.find((i) => i.key === "LLM_PROVIDER")?.value ?? "unknown";
    model = agent?.items.find((i) => i.key === "model")?.value ?? null;
  }
  return (
    <section>
      <h3>Model provider</h3>
      <div className="settings-row">
        <span className="badge">{provider}</span>
        {model && <code>{model}</code>}
        {isAdmin ? (
          <span className="muted">Configured via LLM_PROVIDER on the data-agent service.</span>
        ) : (
          <span className="muted">The agent answers with the provider your admin configured.</span>
        )}
      </div>
    </section>
  );
}

function MemoriesSection() {
  const queryClient = useQueryClient();
  const q = useQuery({ queryKey: ["me", "memories"], queryFn: getMyMemories });
  const [busy, setBusy] = useState<string | null>(null);

  async function forget(id: string) {
    setBusy(id);
    try {
      await deleteMyMemory(id);
      await queryClient.invalidateQueries({ queryKey: ["me", "memories"] });
    } finally {
      setBusy(null);
    }
  }

  return (
    <section>
      <h3>Remembered preferences</h3>
      <p className="muted">
        Durable preferences the agent has stored about how you like answers. Owner-only — even
        admins can't read another user's memories.
      </p>
      {q.isLoading && <p className="muted">Loading…</p>}
      {q.error && <p className="error">{(q.error as Error).message}</p>}
      {q.data && q.data.length === 0 && <p className="muted">Nothing remembered yet.</p>}
      {q.data && q.data.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Preference</th>
                <th>Stored</th>
                <th>Last used</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((m) => (
                <tr key={m.id}>
                  <td className="wide-cell">{m.content}</td>
                  <td>{formatTime(m.created_at)}</td>
                  <td>{m.last_used_at ? formatTime(m.last_used_at) : "-"}</td>
                  <td>
                    <button
                      className="link"
                      disabled={busy === m.id}
                      onClick={() => forget(m.id)}
                    >
                      forget
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function AccessSection({ user }: { user: User }) {
  const q = useQuery({ queryKey: ["me", "access"], queryFn: getMyAccess });
  return (
    <section>
      <h3>My data access</h3>
      <div className="settings-row">
        <span className={`pill role-${user.role}`}>{user.role}</span>
        {q.data && <span className="muted">{q.data.rls_note}</span>}
      </div>
      {q.data && q.data.datasets.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Dataset</th>
                <th>Name</th>
                <th>Status</th>
                <th>Access</th>
              </tr>
            </thead>
            <tbody>
              {q.data.datasets.map((d) => (
                <tr key={d.slug}>
                  <td>
                    <code>{d.slug}</code>
                  </td>
                  <td>{d.name}</td>
                  <td>{d.status}</td>
                  <td>{d.access}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {q.data && q.data.datasets.length === 0 && (
        <p className="muted">
          No dataset grants{user.role === "admin" ? " (admin role reads across users)" : ""}.
        </p>
      )}
    </section>
  );
}

export function SettingsPage({ user }: { user: User }) {
  return (
    <main className="admin settings">
      <section className="admin-band">
        <h2>Settings</h2>
        <p className="muted" style={{ margin: 0 }}>
          {user.display_name} · {user.email}
        </p>
      </section>
      <ThemeSection />
      <ModelSection user={user} />
      <MemoriesSection />
      <AccessSection user={user} />
    </main>
  );
}
