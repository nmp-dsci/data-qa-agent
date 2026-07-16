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
import { setThemePref, ThemePref, useThemePref } from "../../lib/theme";

const THEME_OPTIONS: { value: ThemePref; label: string }[] = [
  { value: "dark", label: "Dark" },
  { value: "light", label: "Light" },
  { value: "system", label: "System" },
];

function ThemeSection() {
  const pref = useThemePref();
  return (
    <section>
      <h3>Appearance</h3>
      <div className="settings-row">
        <span className="seg" role="group" aria-label="Theme">
          {THEME_OPTIONS.map((o) => (
            <button
              key={o.value}
              className={pref === o.value ? "on" : ""}
              aria-pressed={pref === o.value}
              onClick={() => setThemePref(o.value)}
            >
              {o.label}
            </button>
          ))}
        </span>
        <span className="muted">Charts and every panel follow the design tokens · System matches your OS.</span>
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
        <div className="mem-list">
          {q.data.map((m) => (
            <div key={m.id} className="mem-card">
              <div className="mem-body">
                <div className="mem-text">{m.content}</div>
                <div className="mem-meta">
                  learned {formatTime(m.created_at)}
                  {m.last_used_at ? ` · last used ${formatTime(m.last_used_at)}` : ""}
                </div>
              </div>
              <button
                className="btn-ghost mem-forget"
                disabled={busy === m.id}
                onClick={() => forget(m.id)}
              >
                {busy === m.id ? "…" : "forget"}
              </button>
            </div>
          ))}
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
