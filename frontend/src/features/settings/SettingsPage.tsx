// Settings / Profile: theme toggle, model provider, the agent's remembered
// preferences (read/forget — owner-only under RLS), and a data-access summary.
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createServiceAccount,
  deleteMyMemory,
  getAdminConfig,
  getExploreDatasets,
  getMyAccess,
  getMyMemories,
  listServiceAccounts,
  revokeServiceAccount,
  ServiceAccountCreated,
  User,
} from "../../lib/api";
import { formatTime } from "../../lib/format";
import { setAmbientMotion, useAmbientMotion } from "../../lib/motion";
import { setThemePref, ThemePref, useThemePref } from "../../lib/theme";

/** s25: the appearance control speaks the cockpit's language. Labels only —
 *  the underlying ThemePref values, the <html data-theme> resolution and the
 *  OS tracking are all unchanged. */
const THEME_OPTIONS: { value: ThemePref; label: string }[] = [
  { value: "dark", label: "Night" },
  { value: "light", label: "Day" },
  { value: "system", label: "Auto" },
];

function ThemeSection() {
  const pref = useThemePref();
  const motion = useAmbientMotion();
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
        <span className="muted">Charts and every panel follow the design tokens · Auto matches your OS.</span>
      </div>
      <div className="settings-row">
        <span className="seg" role="group" aria-label="Ambient motion">
          {[
            { on: true, label: "Flying" },
            { on: false, label: "Parked" },
          ].map((o) => (
            <button
              key={o.label}
              className={motion === o.on ? "on" : ""}
              aria-pressed={motion === o.on}
              onClick={() => setAmbientMotion(o.on)}
            >
              {o.label}
            </button>
          ))}
        </span>
        <span className="muted">
          The night-flight scene behind every screen · Parked freezes it on one frame. Your OS
          reduced-motion setting always wins.
        </span>
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

/** s35: machine identities for Slack, webhooks and the MCP server.
 *
 *  The whole panel is built around one fact: the key exists exactly once, in
 *  the create response. So the reveal is a deliberate, dismissible block that
 *  says so plainly — if the UI let anyone believe it could be read back later,
 *  they wouldn't copy it, and the only fix is minting another one. */
function ServiceAccountsSection() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin", "service-accounts"], queryFn: listServiceAccounts });
  // Every dataset, NOT the admin's own grants: an admin reads across users via
  // the role and so has no dataset_access rows of their own, which left the
  // picker empty and made it impossible to grant a new key anything.
  const catalog = useQuery({ queryKey: ["explore", "datasets"], queryFn: getExploreDatasets });
  const [name, setName] = useState("");
  const [surface, setSurface] = useState("slack");
  const [datasets, setDatasets] = useState<string[]>([]);
  const [minted, setMinted] = useState<ServiceAccountCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const available = catalog.data ?? [];

  async function create() {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createServiceAccount({
        name: name.trim(),
        surface,
        dataset_slugs: datasets,
      });
      setMinted(created);
      setCopied(false);
      setName("");
      setDatasets([]);
      qc.invalidateQueries({ queryKey: ["admin", "service-accounts"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string) {
    try {
      await revokeServiceAccount(id);
      qc.invalidateQueries({ queryKey: ["admin", "service-accounts"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <section>
      <h3>Service accounts</h3>
      <p className="muted" style={{ marginTop: 0 }}>
        Keys for the non-UI surfaces. A key answers with its own data grants, so grant it only
        what everyone using that surface should see — for Slack, that means the channel it lives in.
      </p>

      {minted && (
        <div className="key-reveal">
          <strong>Copy this key now — it is never shown again.</strong>
          <p className="muted">
            Only the hash is stored, so it cannot be recovered or re-displayed. If you lose it,
            revoke this account and mint another.
          </p>
          <div className="key-reveal-row">
            <code>{minted.key}</code>
            <button
              className="btn-quiet"
              onClick={() => {
                navigator.clipboard?.writeText(minted.key);
                setCopied(true);
              }}
            >
              {copied ? "Copied" : "Copy"}
            </button>
            <button className="btn-quiet" onClick={() => setMinted(null)}>
              Done
            </button>
          </div>
        </div>
      )}

      <div className="sa-form">
        <input
          type="text"
          placeholder="Name (e.g. property channel bot)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-label="Service account name"
        />
        <select value={surface} onChange={(e) => setSurface(e.target.value)} aria-label="Surface">
          <option value="slack">slack</option>
          <option value="webhook">webhook</option>
          <option value="mcp">mcp</option>
        </select>
        <span className="seg" role="group" aria-label="Datasets">
          {available.map((d) => (
            <button
              key={d.slug}
              className={datasets.includes(d.slug) ? "on" : ""}
              aria-pressed={datasets.includes(d.slug)}
              onClick={() =>
                setDatasets((prev) =>
                  prev.includes(d.slug) ? prev.filter((s) => s !== d.slug) : [...prev, d.slug],
                )
              }
            >
              {d.slug}
            </button>
          ))}
        </span>
        <button className="btn-mint" onClick={create} disabled={busy || !name.trim()}>
          {busy ? "Minting…" : "Mint key"}
        </button>
      </div>
      {/* Both of these exist because the button is otherwise silent. An empty
          name disables it, and a key with no datasets mints happily and then
          reads nothing — neither said so, so both looked like "it's broken". */}
      {!name.trim() && <p className="sa-hint">Give the key a name to mint it.</p>}
      {name.trim() && datasets.length === 0 && (
        <p className="sa-hint warn">
          No datasets selected — this key will authenticate but read nothing. Pick at least one.
        </p>
      )}
      {error && <p className="error-text">{error}</p>}

      {q.data && q.data.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Surface</th>
                <th>Key id</th>
                <th>Last used</th>
                <th>State</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {q.data.map((a) => (
                <tr key={a.id}>
                  <td>{a.name}</td>
                  <td>{a.surface}</td>
                  <td>
                    <code>{a.key_id}</code>
                  </td>
                  <td>{a.last_used_at ? formatTime(a.last_used_at) : "never"}</td>
                  <td>{a.revoked_at ? "revoked" : "active"}</td>
                  <td>
                    {!a.revoked_at && (
                      <button className="btn-quiet" onClick={() => revoke(a.id)}>
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {q.data && q.data.length === 0 && <p className="muted">No service accounts yet.</p>}
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
      {user.role === "admin" && <ServiceAccountsSection />}
    </main>
  );
}
