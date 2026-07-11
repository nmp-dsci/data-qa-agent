// Login gate: branded card — Google Sign-in in production, demo profiles on
// the dev-auth stub. Same card in both modes; dev just adds the profile list.
import { useEffect, useRef } from "react";
import { User } from "../lib/api";
import { renderGoogleButton } from "../lib/auth";
import { BrandMark } from "../ui/icons";

const TEST_USERS = [
  { username: "admin", label: "Admin", hint: "sees all data · full trace", initials: "AD", tint: "#f0c674" },
  { username: "user1", label: "User One", hint: "property data access", initials: "U1", tint: "#9ece6a" },
  { username: "user2", label: "User Two", hint: "no data access (isolated)", initials: "U2", tint: "#7dcfff" },
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
        <div className="login-mark">
          <BrandMark size={40} />
        </div>
        <h1>Datapilot</h1>
        <p className="sub">Ask your data anything. Governed answers in seconds.</p>
        {authMode === "google" ? (
          <>
            <div className="users google" ref={btnRef} />
            {error && <p className="error">{error}</p>}
            <p className="foot">Secured by Google Sign-in · row-level security · every query audited</p>
          </>
        ) : (
          <>
            <div className="login-div">sign in as a demo profile</div>
            <div className="users">
              {TEST_USERS.map((u) => (
                <button key={u.username} onClick={() => onDevLogin(u.username)}>
                  <span className="login-av" style={{ background: u.tint }}>
                    {u.initials}
                  </span>
                  <span className="login-who">
                    <strong>{u.label}</strong>
                    <span>{u.hint}</span>
                  </span>
                </button>
              ))}
            </div>
            {error && <p className="error">{error}</p>}
            <p className="foot">Dev-auth stub · production uses Google Sign-in · row-level security</p>
          </>
        )}
      </div>
    </div>
  );
}

export type { User };
