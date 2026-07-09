// Login gate: dev-auth stub (seeded test users) or Entra External ID.
import { User } from "../lib/api";

const TEST_USERS = [
  { username: "admin", label: "Admin", hint: "sees all data" },
  { username: "user1", label: "User One", hint: "has property data access" },
  { username: "user2", label: "User Two", hint: "no data access (isolated)" },
];

export function Login({
  authMode,
  error,
  onDevLogin,
  onEntraLogin,
}: {
  authMode: "dev" | "entra";
  error: string | null;
  onDevLogin: (username: string) => void;
  onEntraLogin: () => void;
}) {
  return (
    <div className="login">
      <div className="login-card">
        <h1>data-qa-agent</h1>
        {authMode === "entra" ? (
          <>
            <p className="sub">Sign in to ask questions about your data.</p>
            <div className="users">
              <button onClick={onEntraLogin}>
                <strong>Sign in with Microsoft</strong>
                <span>Entra External ID</span>
              </button>
            </div>
            {error && <p className="error">{error}</p>}
            <p className="foot">Secured by Microsoft Entra External ID</p>
          </>
        ) : (
          <>
            <p className="sub">Ask questions about your data. Sign in as a test user:</p>
            <div className="users">
              {TEST_USERS.map((u) => (
                <button key={u.username} onClick={() => onDevLogin(u.username)}>
                  <strong>{u.label}</strong>
                  <span>{u.hint}</span>
                </button>
              ))}
            </div>
            {error && <p className="error">{error}</p>}
            <p className="foot">Dev-auth stub · production uses Microsoft Entra External ID</p>
          </>
        )}
      </div>
    </div>
  );
}

export type { User };
