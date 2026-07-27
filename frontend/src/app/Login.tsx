// Login gate (s33 "night flight") — the front door, rebuilt as one composed
// scene with one job: a first-time visitor should understand what Data Pilot
// does and be signed in without scrolling.
//
// The scene is the Canopy canvas (ui/Canopy.tsx) — starfield, a gold horizon
// curved with the planet, and a neon grid terrain flowing toward the viewer.
// The same component runs behind every app screen at ambient strength, so
// signing in doesn't change sky.
//
// What replaced the s25 walkthrough, and why: the old front door told the
// product story as a 4-slide carousel on a 4s timer, which meant a new user
// saw one quarter of the value proposition at a time and had to wait for the
// rest. Everything is visible at once now — a 2×2 benefit grid, a strip of
// real questions you can ask, and an instrument cluster of outcome metrics.
// The card sits directly beside the grid rather than a column away, because
// the gap was doing nothing but making the page feel empty.
//
// Motion: the scene and the heading tape are decorative and freeze under
// prefers-reduced-motion or the Settings "Ambient motion" switch.
// E2E contracts kept: the dev profile buttons' accessible names ("Admin",
// "User One", "User Two" — e2e/helpers.ts clicks them by exact text) and the
// "Data Pilot" heading the visual/a11y specs wait on.
import { useEffect, useRef, useState } from "react";
import { track, User, wakeDb } from "../lib/api";
import { LoginStatus, renderGoogleButton } from "../lib/auth";
import { useAmbientMotion } from "../lib/motion";
import { useMediaQuery } from "../lib/useMediaQuery";
import { Canopy } from "../ui/Canopy";
import { BrandMark } from "../ui/icons";

const TEST_USERS = [
  { username: "admin", label: "Admin", hint: "sees all data · full trace", initials: "AD", tint: "#f2ca79" },
  { username: "user1", label: "User One", hint: "property data access", initials: "U1", tint: "#9ece6a" },
  { username: "user2", label: "User Two", hint: "no data access (isolated)", initials: "U2", tint: "#7dcfff" },
];

// ---------------------------------------------------------------------------
// HUD chrome — a heading tape across the top of the canopy. The two readouts
// beside it are the only "telemetry" on the page and they are the truth about
// the session you are about to start: every query is row-level scoped and
// audited. No invented airspeeds.
// ---------------------------------------------------------------------------
const HEADINGS = ["N", "03", "06", "E", "12", "15", "S", "21", "24", "W", "30", "33"];

function HeadingTape({ moving }: { moving: boolean }) {
  const ticks = Array.from({ length: 32 }, (_, i) => ({ x: i * 25 + 10, major: i % 2 === 0 }));
  return (
    <div className="hud-strip" aria-hidden="true">
      <span className="hud-readout">
        <span className="instrument-label dim">RLS</span>
        <b className="hud-readout-value">ACTIVE</b>
      </span>
      <div className="hud-tape-wrap">
        <svg
          className={moving ? "hud-tape-svg drift" : "hud-tape-svg"}
          viewBox="0 0 800 26"
          preserveAspectRatio="none"
        >
          <g stroke="currentColor" strokeOpacity=".5" fill="none">
            {ticks.map((t) => (
              <line key={t.x} x1={t.x} y1="18" x2={t.x} y2={t.major ? 26 : 23} />
            ))}
          </g>
          <g fontFamily="var(--mono)" fontSize="9" fill="currentColor" fillOpacity=".8" textAnchor="middle">
            {ticks
              .filter((t) => t.major)
              .map((t, i) => (
                <text key={t.x} x={t.x} y="12">
                  {HEADINGS[i % 12]}
                </text>
              ))}
          </g>
        </svg>
        <span className="hud-caret" />
      </div>
      <span className="hud-readout">
        <span className="instrument-label dim">AUDIT</span>
        <b className="hud-readout-value">ON</b>
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// What Data Pilot does — four benefits, all visible at once. Each carries a
// small faithful sketch of the surface it names, built from the same tokens as
// the real thing, so the claim is shown before it is told.
// ---------------------------------------------------------------------------
function BenefitAsk() {
  return (
    <>
      <div className="bene-bubble">Which suburbs had the fastest rent growth last year?</div>
      <div className="bene-mini">
        <div className="bene-tiles">
          <span>
            $612<small>avg weekly rent</small>
          </span>
          <span>
            +8.4%<small>year on year</small>
          </span>
        </div>
        <svg className="bene-spark" viewBox="0 0 240 34" preserveAspectRatio="none" aria-hidden="true">
          <path className="fill" d="M3 28 46 22 89 24 132 14 175 11 237 3V34H3Z" />
          <polyline points="3,28 46,22 89,24 132,14 175,11 237,3" />
          <circle cx="237" cy="3" r="3" />
        </svg>
      </div>
    </>
  );
}

function BenefitTune() {
  return (
    <div className="bene-rows">
      <span className="bene-row">
        ★ Median rent by suburb <i>ready</i>
      </span>
      <span className="bene-row">
        ★ Sale price trend, houses <i>graded</i>
      </span>
      <span className="bene-row">
        ★ Yield by postcode <i>ready</i>
      </span>
    </div>
  );
}

function BenefitExplore() {
  return (
    <>
      <div className="bene-chips">
        <span className="t">target · Hornsby houses</span>
        <span className="c">vs · Newcastle houses</span>
      </div>
      {/* Five paired bars on a baseline — the shape the Explore tool actually
          draws, so the sketch is a small true picture rather than blocks. */}
      <svg className="bene-bars" viewBox="0 0 240 34" preserveAspectRatio="none" aria-hidden="true">
        {[
          [17, 26],
          [24, 15],
          [12, 19],
          [28, 21],
          [21, 11],
        ].map(([a, b], i) => (
          <g key={i} transform={`translate(${14 + i * 45} 0)`}>
            <rect className="b1" x="0" y={31 - a} width="10" height={a} rx="2" />
            <rect className="b2" x="13" y={31 - b} width="10" height={b} rx="2" />
          </g>
        ))}
        <line className="base" x1="0" y1="32" x2="240" y2="32" />
      </svg>
    </>
  );
}

function BenefitDig() {
  return (
    <>
      <code className="bene-code">
        <b>select</b> suburb, avg(weekly_rent)…
        <br />
        <b>where</b> dwelling_type = 'house'…
      </code>
      <div className="bene-result">
        <span>hornsby · 748 · n=1.2k</span>
        <span className="bene-run">▶ run</span>
      </div>
    </>
  );
}

const BENEFITS = [
  {
    key: "ask",
    step: "01",
    label: "Ask",
    title: "Ask in plain English",
    body: "The agent plans governed SQL over your warehouse and lands a full report — KPIs, charts, and the queries behind them. Share it the moment it arrives.",
    Visual: BenefitAsk,
  },
  {
    key: "tune",
    step: "02",
    label: "Tune",
    title: "Tuned to how your team reads data",
    body: "Admins coach the agent with golden answers and data knowledge, so replies use your organisation's own definitions — not a generic guess.",
    Visual: BenefitTune,
  },
  {
    key: "explore",
    step: "03",
    label: "Explore",
    title: "Know the data before you ask",
    body: "Profile any dataset, compare two cohorts side by side, and track trends — all without writing a line of SQL.",
    Visual: BenefitExplore,
  },
  {
    key: "dig",
    step: "04",
    label: "Dig",
    title: "Go hands-on when it matters",
    body: "A read-only, row-level-scoped SQL editor for the ad-hoc moment. Every chart links back to its query, so an answer is never a black box.",
    Visual: BenefitDig,
  },
] as const;

/** Real questions, in the shape people actually type them. These are the first
 *  thing a new visitor can picture themselves doing, so they lead with the
 *  question and never with the feature. */
const EXAMPLE_QUESTIONS = [
  "Which suburbs had the fastest rent growth last year?",
  "House vs unit sale prices in Hornsby since 2010",
  "Where are rental yields above 4%?",
  "Compare 2023 and 2022 weekly rent for 3-bedroom houses",
  "Top 10 postcodes by sales volume this quarter",
  "Has the median sale price in Newcastle recovered yet?",
];

/** Outcome metrics, not deployment telemetry: every one of these is a property
 *  of the product that holds on any warehouse it is pointed at, which is why
 *  none of them is a counter that would be a lie on a fresh install. */
const IMPACT = [
  { value: "≈2 min", label: "question → shareable report" },
  { value: "0", label: "lines of SQL to write" },
  { value: "100%", label: "results row-level scoped" },
  { value: "every chart", label: "opens the query behind it" },
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
  const reduced = useMediaQuery("(prefers-reduced-motion: reduce)");
  const motionOn = useAmbientMotion();
  // s29: sign-in progress. Set the moment Google hands back a credential —
  // before this, a login waiting on the /me exchange (worst case: an Aurora
  // resume, ~30s) was pixel-identical to a dead button, and users answered
  // the silence by signing in 4-5 times. Cleared on any terminal outcome.
  const [signing, setSigning] = useState<LoginStatus | null>(null);

  useEffect(() => {
    if (authMode !== "google" || !btnRef.current) return;
    // Start the database resuming now (F3): the Google dance takes ~12s, the
    // wake ~30s — every second overlapped is a second the user never waits.
    wakeDb();
    const fail = (e: Error) => {
      setSigning(null);
      onError(e.message);
    };
    renderGoogleButton(
      btnRef.current,
      (u) => {
        setSigning(null);
        onUser(u);
      },
      fail,
      setSigning,
    ).catch((e) => fail(e as Error));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authMode]);

  useEffect(() => {
    track("login_benefits_view", { benefits: BENEFITS.length });
  }, []);

  return (
    <div className="login">
      <Canopy variant="login" />
      <HeadingTape moving={!reduced && motionOn} />

      <div className="login-stage">
        <div className="login-card">
          {/* The mark in a HUD reticle: the card is the boresight of the page. */}
          <div className="login-mark">
            <span className="login-reticle" aria-hidden="true" />
            <BrandMark size={44} />
          </div>
          <h1 className="login-title">
            Data <em>Pilot</em>
          </h1>
          <p className="login-tagline">Cleared for insight.</p>

          {authMode === "google" ? (
            <>
              <div className="login-div">sign in</div>
              {/* The GIS iframe stays mounted while hidden — display:none via
                  the class, never unmount, or the credential callback dies
                  with it. */}
              <div className={signing ? "users google signing" : "users google"} ref={btnRef} />
              {signing && (
                <div className="login-signing" role="status">
                  <span className="login-signing-title">Signing you in…</span>
                  {signing.phase === "warming" && (
                    <>
                      <span className="annunciator warn">Waking warehouse · {signing.waitedS}s</span>
                      <span className="login-signing-note">
                        First visit after an idle hour spins the database up — no action needed.
                      </span>
                    </>
                  )}
                </div>
              )}
              {error && !signing && (
                <p className="error" role="alert">
                  {error}
                </p>
              )}
              {/* Preflight lamps carry the guarantees in the cockpit's own
                  clipped voice; the impact cluster spells them out in full. */}
              <div className="annunciators login-preflight">
                <span className="annunciator on">Google SSO</span>
                <span className="annunciator on">RLS</span>
                <span className="annunciator on">Audited</span>
              </div>
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
              {error && (
                <p className="error" role="alert">
                  {error}
                </p>
              )}
              <div className="annunciators login-preflight">
                <span className="annunciator warn">Dev auth</span>
                <span className="annunciator on">RLS</span>
                <span className="annunciator on">Audited</span>
              </div>
            </>
          )}
        </div>

        <section className="login-story" aria-label="What Data Pilot does">
          <p className="login-lede">
            <span className="instrument-label accent">What Data Pilot does</span>
            <span>
              Ask a question, get a boardroom-ready report — no analyst queue, no dashboard hunt.
            </span>
          </p>

          <ul className="bene-grid">
            {BENEFITS.map((b) => (
              <li key={b.key} className="bene-cell">
                <div className="bene-head">
                  <span className="bene-step">{b.step}</span>
                  <span className="instrument-label">{b.label}</span>
                </div>
                <b className="bene-title">{b.title}</b>
                <div className="bene-visual">
                  <b.Visual />
                </div>
                <p className="bene-body">{b.body}</p>
              </li>
            ))}
          </ul>

          <div className="login-asks">
            <span className="instrument-label dim">Questions people ask it</span>
            <ul>
              {EXAMPLE_QUESTIONS.map((q) => (
                <li key={q}>{q}</li>
              ))}
            </ul>
          </div>
        </section>
      </div>

      <div className="login-impact">
        {IMPACT.map((m) => (
          <div key={m.label} className="login-imp">
            <b>{m.value}</b>
            <small>{m.label}</small>
          </div>
        ))}
      </div>
    </div>
  );
}

export type { User };
