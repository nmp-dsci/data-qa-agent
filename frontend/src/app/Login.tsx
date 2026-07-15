// Login gate (s17): a split front door. Left — the Data Pilot sign-in card in a
// cockpit scene (canopy pillars, HUD, drifting data glyphs, a jet + contrail,
// an instrument dash). Right — a self-advancing flight-path walkthrough of the
// five product promises: the plane hops waypoints on a 4s timer (pausable on
// hover/focus, keyboard + dots navigable) and lights each promise as it lands.
// Pure CSS/SVG, ~0 asset weight; static + no auto-advance under reduced motion.
// Google Sign-in in production, demo profiles on the dev-auth stub — same card.
import { useEffect, useRef, useState } from "react";
import { track, User } from "../lib/api";
import { renderGoogleButton } from "../lib/auth";
import { useMediaQuery } from "../lib/useMediaQuery";
import { BrandMark } from "../ui/icons";

const TEST_USERS = [
  { username: "admin", label: "Admin", hint: "sees all data · full trace", initials: "AD", tint: "#f2ca79" },
  { username: "user1", label: "User One", hint: "property data access", initials: "U1", tint: "#9ece6a" },
  { username: "user2", label: "User Two", hint: "no data access (isolated)", initials: "U2", tint: "#7dcfff" },
];

// Five product promises, each pinned to a waypoint on the flight path (viewBox
// 360×120). `angle` is the plane's heading (deg) along the route toward the next
// stop; `ldy` nudges the map label clear of the arc.
type Slide = {
  key: string;
  label: string;
  x: number;
  y: number;
  angle: number;
  ldy: number;
  title: string;
  tag: string;
  story: string;
};
const SLIDES: Slide[] = [
  {
    key: "data", label: "DATA", x: 18, y: 96, angle: -34, ldy: 15,
    title: "All your data, one spot", tag: "landed · modelled · served",
    story: "Every source landed, modelled and served from a single governed warehouse.",
  },
  {
    key: "ask", label: "ASK", x: 100, y: 40, angle: 15, ldy: -15,
    title: "Ask. Answer. Share.", tag: "NL → governed SQL → report",
    story: "Plain-English questions become high-quality reports you can share instantly.",
  },
  {
    key: "govern", label: "GOVERN", x: 180, y: 62, angle: -19, ldy: 16,
    title: "Governed access", tag: "RLS + full audit trail",
    story: "Row-level security means every user sees exactly the data they're allowed to — and every query is audited.",
  },
  {
    key: "train", label: "TRAIN", x: 262, y: 34, angle: -10, ldy: -15,
    title: "Train it your way", tag: "golden examples steer answers",
    story: "Author golden examples so answers arrive the way your business reads data.",
  },
  {
    key: "tailor", label: "TAILOR", x: 340, y: 20, angle: -10, ldy: -14,
    title: "Remembers each user", tag: "per-user memory tailors answers",
    story: "Per-user memory tailors how answers are computed and presented over time.",
  },
];

const PATH_D =
  "M18 96 C 52 78 70 40 100 40 C 135 40 150 62 180 62 C 215 62 232 34 262 34 C 295 34 318 20 340 20";
const PLANE_D = "M8 0 L-6 -5 L-2 0 L-6 5 Z";

function Walkthrough() {
  const reduced = useMediaQuery("(prefers-reduced-motion: reduce)");
  const [active, setActive] = useState(0);
  const [paused, setPaused] = useState(false);
  const n = SLIDES.length;

  // 4s auto-advance; the timer resets on every change (manual or auto) and is
  // suspended while paused (hover/focus) or under reduced motion.
  useEffect(() => {
    if (paused || reduced) return;
    const id = window.setTimeout(() => setActive((a) => (a + 1) % n), 4000);
    return () => window.clearTimeout(id);
  }, [active, paused, reduced, n]);

  useEffect(() => {
    track("login_walkthrough_view", { slide: SLIDES[active].key, index: active });
  }, [active]);

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowRight" || e.key === "ArrowDown") setActive((a) => (a + 1) % n);
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") setActive((a) => (a - 1 + n) % n);
    else if (e.key === "Home") setActive(0);
    else if (e.key === "End") setActive(n - 1);
    else return;
    e.preventDefault();
  }

  const slide = SLIDES[active];
  const litOffset = 100 - (n > 1 ? active / (n - 1) : 1) * 100;

  return (
    <div
      className="walk"
      role="group"
      aria-label="Data Pilot product walkthrough"
      tabIndex={0}
      onKeyDown={onKeyDown}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <div className="walk-map">
        <svg className="walk-fp" viewBox="0 0 360 120" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
          <path className="fp-route" d={PATH_D} pathLength={100} />
          <path
            className="fp-lit"
            d={PATH_D}
            pathLength={100}
            strokeDasharray="100"
            strokeDashoffset={litOffset}
          />
          {SLIDES.map((s, i) => (
            <circle key={s.key} className={i <= active ? "fp-wp lit" : "fp-wp"} cx={s.x} cy={s.y} r={4} />
          ))}
          <g className="fp-plane" style={{ transform: `translate(${slide.x}px, ${slide.y}px) rotate(${slide.angle}deg)` }}>
            <path d={PLANE_D} />
          </g>
        </svg>
        {SLIDES.map((s, i) => (
          <span
            key={s.key}
            className={i === active ? "walk-wl on" : "walk-wl"}
            style={{ left: `${(s.x / 360) * 100}%`, top: `calc(${(s.y / 120) * 100}% + ${s.ldy}px)` }}
          >
            {s.label}
          </span>
        ))}
      </div>

      <div className="walk-story">
        <h2>{slide.title}</h2>
        <p>{slide.story}</p>
      </div>

      <ol className="walk-props">
        {SLIDES.map((s, i) => (
          <li key={s.key} className={i === active ? "walk-prop lit" : "walk-prop"}>
            <span className="walk-prop-i">{i + 1}</span>
            <div>
              <b>{s.title}</b>
              <span>{s.tag}</span>
            </div>
          </li>
        ))}
      </ol>

      <div className="walk-dots" role="tablist" aria-label="Walkthrough slides">
        {SLIDES.map((s, i) => (
          <button
            key={s.key}
            role="tab"
            aria-selected={i === active}
            aria-label={s.title}
            className={i === active ? "walk-dot on" : "walk-dot"}
            onClick={() => setActive(i)}
          />
        ))}
      </div>
    </div>
  );
}

function Cockpit() {
  return (
    <div className="cockpit" aria-hidden="true">
      <span className="cockpit-htape">&#8249; 020 &middot; 030 &middot; 040 &#8250;</span>
      <span className="cockpit-hud" />
      <span className="cockpit-glyph big" style={{ top: "6%", left: "7%" }}>
        42%
      </span>
      <span className="cockpit-glyph dim" style={{ top: "6%", right: "11%" }}>
        SELECT *
      </span>
      <svg className="cockpit-jet" viewBox="0 0 240 60" aria-hidden="true">
        <path className="jet-trail" d="M4 52 C 70 46, 150 32, 200 18" />
        <path className="jet-body" d="M200 18 l 24 -7 l -17 13 l -3 -4 z" />
      </svg>
      <div className="cockpit-dash">
        <b />
        <i />
        <i />
        <i />
        <b />
      </div>
    </div>
  );
}

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
      <div className="login-left">
        <Cockpit />
        <div className="login-card">
          <div className="login-mark">
            <BrandMark size={44} />
          </div>
          <h1 className="login-title">
            Data <em>Pilot</em>
          </h1>
          <p className="login-tagline">Your data, flown right.</p>
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
      <Walkthrough />
    </div>
  );
}

export type { User };
