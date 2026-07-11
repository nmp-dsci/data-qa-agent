// Login gate: dev-auth stub (seeded test users) or Google Sign-in.
import { useEffect, useRef } from "react";
import { User } from "../lib/api";
import { renderGoogleButton } from "../lib/auth";

const TEST_USERS = [
  { username: "admin", label: "Admin", hint: "sees all data" },
  { username: "user1", label: "User One", hint: "has property data access" },
  { username: "user2", label: "User Two", hint: "no data access (isolated)" },
];

export function Login({
  authMode,
  error,
  onDevLogin,
  onUser,
  onError,
}: {
  authMode: "dev" | "google";
  error: string | null;
  onDevLogin: (username: string) => void;
  onUser: (user: User) => void;
  onError: (message: string) => void;
}) {
  const btnRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (authMode !== "google" || !btnRef.current) return;
    renderGoogleButton(btnRef.current, onUser, (e) => onError(e.message)).catch((e) =>
      onError((e as Error).message),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authMode]);

  return (
    <div className="login">
      <div className="login-card">
        <h1>Datapilot</h1>
        {authMode === "google" ? (
          <>
            <p className="sub">Sign in to ask questions about your data.</p>
            <div className="users" ref={btnRef} />
            {error && <p className="error">{error}</p>}
            <p className="foot">Secured by Google Sign-in</p>
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
            <p className="foot">Dev-auth stub · production uses Google Sign-in</p>
          </>
        )}
      </div>
    </div>
  );
}

export type { User };
