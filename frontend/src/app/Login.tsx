// Login gate (s25 "Flight Deck") — the split front door, rebuilt around one
// composed scene instead of scattered props. A single full-bleed canopy layer
// sits behind BOTH panels: dawn horizon, perspective ground grid, canopy
// vignette, and three aircraft on crossing airways. The hero — "the Sortie",
// the brand mark's airliner inverted out of its gold tile — flies the product
// story's route left-to-right, lighting each waypoint as it lands on it.
//
// Motion rules (s25): planes always travel ALONG a path — the Sortie hops
// waypoint-to-waypoint with CSS offset-path/offset-distance (this is the fix
// for the s17 corner-cutting bug, where a point-to-point translate visibly
// left the curve), and ambient traffic flies its airways on CSS loops. HUD
// readouts drift inside realistic bounds on tabular numerals, so the cockpit
// feels airborne while the layout never wobbles. Everything decorative
// freezes under prefers-reduced-motion into a designed still: the Sortie
// parks on the current waypoint with its contrail burned in behind it, and
// the traffic holds position on its airways.
//
// Pure CSS/SVG, ~0 asset weight. Google Sign-in in production, demo profiles
// on the dev-auth stub — same card. E2E contracts kept: the profile buttons'
// accessible names, the dot `role=tablist`, and the login_walkthrough_view
// track() events are unchanged from s17.
import { useEffect, useMemo, useRef, useState } from "react";
import { track, User } from "../lib/api";
import { renderGoogleButton } from "../lib/auth";
import { useMediaQuery } from "../lib/useMediaQuery";
import { BrandMark, PLANE_PATH_D } from "../ui/icons";

const TEST_USERS = [
  { username: "admin", label: "Admin", hint: "sees all data · full trace", initials: "AD", tint: "#f2ca79" },
  { username: "user1", label: "User One", hint: "property data access", initials: "U1", tint: "#9ece6a" },
  { username: "user2", label: "User Two", hint: "no data access (isolated)", initials: "U2", tint: "#7dcfff" },
];

// ---------------------------------------------------------------------------
// The scene. One 1200×800 viewBox sliced to fill the viewport, so every airway
// scales with the window and the composition crops rather than distorts.
// ---------------------------------------------------------------------------
const SCENE_W = 1200;
const SCENE_H = 800;
const HORIZON_Y = 500;

/** The hero airway — the route the product story is told along. Waypoints sit
 *  on the cubic segment joins so the Sortie lands exactly on each dot.
 *
 *  Shaped as a departure profile — a low run-in, then a climb-out — for one
 *  hard reason: every waypoint has to stay in the visible corridor. The card
 *  covers roughly x 60–420 / y 185–615 and the story panel is glass but not
 *  transparent, so a route that swept diagonally across the full width would
 *  fly its last two waypoints behind the story cards and the Sortie would
 *  simply vanish for half the loop. Climbing up the gutter between the card
 *  and the story column keeps all four lit waypoints — and the plane — on
 *  screen from 1000px up. */
const ROUTE_D =
  "M-60 790 C 60 782 150 740 240 700 C 320 668 380 660 430 650 " +
  "C 500 626 532 540 540 430 C 546 348 553 298 570 240 C 592 166 616 62 640 -60";

/** Crossing traffic — other aircraft, other directions, other speeds. The
 *  cap is three aircraft total (hero + these two); hierarchy is held by size
 *  and brightness, so the scene reads composed rather than busy. */
const TRAFFIC = [
  {
    key: "eastbound",
    // High and blue, left → right, well above the horizon.
    d: "M-60 168 C 240 128 620 196 1260 96",
    cls: "air-2",
    size: 15,
    dur: 34,
    park: 0.36,
  },
  {
    key: "westbound",
    // Low and gold, right → left, skimming the ground grid.
    d: "M1260 636 C 900 692 400 616 -60 664",
    cls: "air-3",
    size: 13,
    dur: 47,
    park: 0.58,
  },
] as const;

type Waypoint = {
  key: string;
  label: string;
  x: number;
  y: number;
  title: string;
  story: string;
};

/** Four outcome waypoints (s25 copy rewrite): the product story leads with what
 *  the user gets, not how the plumbing works. Governance moved out of the slide
 *  deck and into the card's preflight lamps + the capstone, where trust is
 *  actually read. */
const WAYPOINTS: Waypoint[] = [
  {
    key: "ask",
    label: "ASK",
    x: 240,
    y: 700,
    title: "Ask in plain English",
    story:
      "The agent plans governed SQL over your warehouse and lands a rich insight report — KPIs, charts, and the queries behind them. Share it the moment it arrives.",
  },
  {
    key: "tune",
    label: "TUNE",
    x: 430,
    y: 650,
    title: "Answers tuned to your business",
    story:
      "Admins coach the agent with golden examples and data knowledge, so every answer reads the way your organisation reads data.",
  },
  {
    key: "explore",
    label: "EXPLORE",
    x: 540,
    y: 430,
    title: "Know your data before you ask",
    story:
      "Profile any dataset, compare cohorts, and track trends in the Explore tool — no SQL required.",
  },
  {
    key: "dig",
    label: "DIG",
    x: 570,
    y: 240,
    title: "Go hands-on when it matters",
    story:
      "A governed SQL editor for ad-hoc investigation. Every chart links back to its query, so an answer is never a black box.",
  },
];

/** Where each waypoint sits along the route, as a 0–1 fraction of its length.
 *  Measured from the real path once on mount (rather than hardcoded) so the
 *  Sortie parks precisely on the dot and the lit contrail ends there too. */
function useRouteFractions(pathRef: React.RefObject<SVGPathElement | null>): number[] {
  const fallback = useMemo(
    () => WAYPOINTS.map((_, i) => (i + 1) / (WAYPOINTS.length + 1)),
    [],
  );
  const [fracs, setFracs] = useState<number[]>(fallback);

  useEffect(() => {
    const path = pathRef.current;
    if (!path || typeof path.getTotalLength !== "function") return;
    const total = path.getTotalLength();
    if (!total) return;
    // Sample the path once and take, for each waypoint, the closest sample.
    // 400 steps ≈ 0.25% precision — visually exact and fully deterministic,
    // so visual baselines don't move between runs.
    const STEPS = 400;
    const samples: { len: number; x: number; y: number }[] = [];
    for (let i = 0; i <= STEPS; i++) {
      const len = (total * i) / STEPS;
      const p = path.getPointAtLength(len);
      samples.push({ len, x: p.x, y: p.y });
    }
    setFracs(
      WAYPOINTS.map((w) => {
        let best = samples[0];
        let bestD = Infinity;
        for (const s of samples) {
          const d = (s.x - w.x) ** 2 + (s.y - w.y) ** 2;
          if (d < bestD) {
            bestD = d;
            best = s;
          }
        }
        return best.len / total;
      }),
    );
  }, [pathRef]);

  return fracs;
}

/** The Sortie and its traffic, plus the sky they fly in. Entirely decorative:
 *  one aria-hidden layer, never focusable, never announced. */
function Canopy({ active, reduced }: { active: number; reduced: boolean }) {
  const routeRef = useRef<SVGPathElement>(null);
  const fracs = useRouteFractions(routeRef);
  // The contrail ends where the Sortie is, always. Under reduced motion this
  // composition simply stops moving — the plane parks on whichever waypoint
  // the reader has navigated to, contrail burned in behind it, traffic holding
  // position. A designed still, and one that still agrees with the story on
  // screen (a fixed park at the last waypoint would contradict it).
  const lit = fracs[active] ?? 0;
  const planeAt = lit;

  return (
    <div className="canopy" aria-hidden="true">
      <div className="canopy-sky" />
      <svg
        className="canopy-svg"
        viewBox={`0 0 ${SCENE_W} ${SCENE_H}`}
        preserveAspectRatio="xMidYMid slice"
        aria-hidden="true"
      >
        <defs>
          {/* The ground grid fades out as it approaches the horizon, so the
              perspective reads as distance rather than as a flat pattern. */}
          <linearGradient id="dp-ground-fade" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#fff" stopOpacity="0" />
            <stop offset="0.45" stopColor="#fff" stopOpacity="0.55" />
            <stop offset="1" stopColor="#fff" stopOpacity="1" />
          </linearGradient>
          <mask id="dp-ground-mask">
            <rect
              x="0"
              y={HORIZON_Y}
              width={SCENE_W}
              height={SCENE_H - HORIZON_Y}
              fill="url(#dp-ground-fade)"
            />
          </mask>
        </defs>

        {/* -- dawn horizon: the 1px line every scene hangs on -- */}
        <line
          className="canopy-horizon"
          x1="0"
          y1={HORIZON_Y}
          x2={SCENE_W}
          y2={HORIZON_Y}
        />

        {/* -- perspective ground grid: rails converging on the vanishing
               point, with recede lines spaced by a squared ramp -- */}
        <g className="canopy-ground" mask="url(#dp-ground-mask)">
          {Array.from({ length: 19 }, (_, i) => {
            const spread = (i - 9) / 9; // -1 … 1
            return (
              <line
                key={`r${i}`}
                x1={SCENE_W / 2 + spread * 90}
                y1={HORIZON_Y}
                x2={SCENE_W / 2 + spread * 2100}
                y2={SCENE_H}
              />
            );
          })}
          {Array.from({ length: 9 }, (_, i) => {
            const t = (i + 1) / 9;
            const y = HORIZON_Y + (SCENE_H - HORIZON_Y) * t * t;
            return <line key={`c${i}`} x1="0" y1={y} x2={SCENE_W} y2={y} />;
          })}
        </g>

        {/* -- crossing traffic: dim airways, ambient loops -- */}
        {TRAFFIC.map((t) => (
          <g key={t.key} className={`airway ${t.cls}`}>
            <path className="airway-route" d={t.d} />
            <g
              className="airway-plane"
              style={{
                offsetPath: `path("${t.d}")`,
                offsetRotate: "auto",
                ...(reduced
                  ? { offsetDistance: `${t.park * 100}%` }
                  : { animation: `dp-fly ${t.dur}s linear infinite` }),
              }}
            >
              <g transform={`rotate(90) scale(${t.size / 100}) translate(-50,-50)`}>
                <path d={PLANE_PATH_D} />
              </g>
            </g>
          </g>
        ))}

        {/* -- the hero airway: the product story's route -- */}
        <g className="airway air-1">
          <path className="airway-route hero-route" ref={routeRef} d={ROUTE_D} />
          <path
            className="hero-lit"
            d={ROUTE_D}
            pathLength={100}
            strokeDasharray="100"
            strokeDashoffset={100 - lit * 100}
          />
          {WAYPOINTS.map((w, i) => (
            <g key={w.key} className={i <= active ? "hero-wp lit" : "hero-wp"}>
              <circle className="hero-wp-halo" cx={w.x} cy={w.y} r="14" />
              <circle className="hero-wp-dot" cx={w.x} cy={w.y} r="4" />
            </g>
          ))}
          {/* The Sortie. offset-path keeps it ON the curve through every hop —
              the s17 build translated point-to-point and cut the corners. */}
          <g
            className="sortie"
            style={{
              offsetPath: `path("${ROUTE_D}")`,
              offsetRotate: "auto",
              offsetDistance: `${planeAt * 100}%`,
            }}
          >
            <g transform="rotate(90) scale(0.30) translate(-50,-50)">
              <path d={PLANE_PATH_D} />
            </g>
          </g>
        </g>
      </svg>

      {/* -- canopy vignette + pillars: pulls the eye to card and story -- */}
      <div className="canopy-vignette" />
      <span className="canopy-pillar left" />
      <span className="canopy-pillar right" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Live HUD readouts — instruments, not decoration: the numbers drift inside
// realistic bounds on tabular numerals, so nothing shifts as they tick.
// ---------------------------------------------------------------------------
function useDrift(base: number, swing: number, reduced: boolean): number {
  const [v, setV] = useState(base);
  useEffect(() => {
    if (reduced) {
      setV(base);
      return;
    }
    const id = window.setInterval(() => {
      setV(base + Math.round((Math.random() * 2 - 1) * swing));
    }, 1500);
    return () => window.clearInterval(id);
  }, [base, swing, reduced]);
  return v;
}

function HudStrip({ reduced }: { reduced: boolean }) {
  const gs = useDrift(240, 12, reduced);
  const fl = useDrift(360, 4, reduced);
  return (
    <div className="hud-strip" aria-hidden="true">
      <span className="hud-readout">
        <span className="instrument-label dim">GS</span>
        <b className="hud-readout-value">{gs}</b>
      </span>
      <span className="hud-tape">&#8249; 020 &middot; 030 &middot; 040 &#8250;</span>
      <span className="hud-readout">
        <span className="instrument-label dim">FL</span>
        <b className="hud-readout-value">{fl}</b>
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Waypoint micro-mocks — show, then tell. Each is a small, faithful sketch of
// the real surface the waypoint is describing, built from the same tokens.
// ---------------------------------------------------------------------------

/** ASK — a mini insight report page: the payoff, in one glance. */
function MockAsk() {
  return (
    <div className="wp-mock mock-ask">
      <div className="mock-head">
        <span className="instrument-label dim">Page 1 · Report</span>
        <span className="mock-star">★ save as golden</span>
      </div>
      <div className="mock-title">Hornsby average sale price — house vs unit, 2010 → 2026</div>
      <div className="mock-legend">
        <span className="mock-key k1">house</span>
        <span className="mock-key k2">unit</span>
      </div>
      <div className="mock-kpis">
        <div className="hud-box">
          <span className="hud-box-label">House avg price</span>
          <span className="hud-box-value">$1,182,406</span>
          <span className="mock-delta">▲ 33.1% · 5 yr</span>
        </div>
        <div className="hud-box">
          <span className="hud-box-label">Unit avg price</span>
          <span className="hud-box-value">$742,180</span>
          <span className="mock-delta">▲ 41.6% · 5 yr</span>
        </div>
      </div>
      <svg className="mock-chart" viewBox="0 0 220 56" preserveAspectRatio="none" aria-hidden="true">
        <path className="mock-line l1" d="M2 48 C 40 44 70 32 104 26 C 140 20 180 12 218 6" />
        <path className="mock-line l2" d="M2 52 C 40 50 70 45 104 40 C 140 35 180 30 218 24" />
      </svg>
    </div>
  );
}

/** TUNE — a golden example, the admin's coaching signal. */
function MockTune() {
  return (
    <div className="wp-mock mock-tune">
      <div className="mock-head">
        <span className="instrument-label dim">Golden example</span>
        <span className="mock-star">★ curated by admin</span>
      </div>
      <div className="mock-quote">&ldquo;rent trends 2077 vs 2076&rdquo;</div>
      <div className="mock-note">
        → answers now open with the <b>bedroom-band breakdown</b> — the way your team reads rent.
      </div>
    </div>
  );
}

/** EXPLORE — the two cohorts, in the Explore tool's own gold/blue pairing. */
function MockExplore() {
  return (
    <div className="wp-mock mock-explore">
      <div className="mock-cohorts">
        <div className="mock-cohort target">
          <span className="instrument-label dim">Target</span>
          <b>$740</b>
          <svg viewBox="0 0 90 26" preserveAspectRatio="none" aria-hidden="true">
            <path d="M1 22 C 18 20 34 14 52 11 C 68 8 78 6 89 3" />
          </svg>
        </div>
        <div className="mock-cohort compare">
          <span className="instrument-label dim">Compare</span>
          <b>$525</b>
          <svg viewBox="0 0 90 26" preserveAspectRatio="none" aria-hidden="true">
            <path d="M1 23 C 18 22 34 19 52 17 C 68 15 78 13 89 11" />
          </svg>
        </div>
      </div>
    </div>
  );
}

/** DIG — the governed SQL editor, with the guarantees that make it safe. */
function MockDig() {
  return (
    <div className="wp-mock mock-dig">
      <code className="mock-sql">
        <b>select</b> suburb, avg_rent <b>from</b> marts.property_rent …
      </code>
      <div className="annunciators">
        <span className="annunciator on">Read-only</span>
        <span className="annunciator on">RLS-scoped</span>
        <span className="annunciator on">Audited</span>
      </div>
    </div>
  );
}

const MOCKS: Record<string, () => React.JSX.Element> = {
  ask: MockAsk,
  tune: MockTune,
  explore: MockExplore,
  dig: MockDig,
};

// ---------------------------------------------------------------------------
// The story panel — glass over the scene, so airways pass behind it like
// traffic behind a windshield.
// ---------------------------------------------------------------------------
function Walkthrough({
  active,
  setActive,
  setPaused,
}: {
  active: number;
  setActive: (fn: (a: number) => number) => void;
  setPaused: (v: boolean) => void;
}) {
  const n = WAYPOINTS.length;

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowRight" || e.key === "ArrowDown") setActive((a) => (a + 1) % n);
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") setActive((a) => (a - 1 + n) % n);
    else if (e.key === "Home") setActive(() => 0);
    else if (e.key === "End") setActive(() => n - 1);
    else return;
    e.preventDefault();
  }

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
      onTouchStart={() => setPaused(true)}
    >
      <div className="walk-inner">
        <p className="walk-lede">
          <span className="instrument-label accent">From plain question to shareable insight.</span>
          <span>
            No analyst queue, no dashboard hunt — ask, and land a boardroom-ready report in about a
            minute.
          </span>
        </p>

        <ol className="walk-props">
          {WAYPOINTS.map((w, i) => {
            const Mock = MOCKS[w.key];
            const on = i === active;
            return (
              <li key={w.key} className={on ? "walk-prop lit" : "walk-prop"}>
                <div className="walk-prop-head">
                  <span className="walk-prop-i">{String(i + 1).padStart(2, "0")}</span>
                  <span className="instrument-label walk-prop-wp">{w.label}</span>
                </div>
                <b>{w.title}</b>
                <span className="walk-prop-story">{w.story}</span>
                {on && Mock && <Mock />}
              </li>
            );
          })}
        </ol>

        <div className="walk-capstone">
          <p>&ldquo;A data team&rsquo;s power, without the back office.&rdquo;</p>
          <span>Row-level security scopes every query to what each user may see.</span>
        </div>

        <div className="walk-dots" role="tablist" aria-label="Walkthrough slides">
          {WAYPOINTS.map((w, i) => (
            <button
              key={w.key}
              role="tab"
              aria-selected={i === active}
              aria-label={w.title}
              className={i === active ? "walk-dot on" : "walk-dot"}
              onClick={() => setActive(() => i)}
            />
          ))}
        </div>
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
  const reduced = useMediaQuery("(prefers-reduced-motion: reduce)");
  const [active, setActive] = useState(0);
  const [paused, setPaused] = useState(false);
  const n = WAYPOINTS.length;

  useEffect(() => {
    if (authMode !== "google" || !btnRef.current) return;
    renderGoogleButton(btnRef.current, onUser, (e) => onError(e.message)).catch((e) =>
      onError((e as Error).message),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authMode]);

  // 4s auto-advance; the timer resets on every change (manual or auto) and is
  // suspended while paused (hover/focus/touch) or under reduced motion.
  useEffect(() => {
    if (paused || reduced) return;
    const id = window.setTimeout(() => setActive((a) => (a + 1) % n), 4000);
    return () => window.clearTimeout(id);
  }, [active, paused, reduced, n]);

  useEffect(() => {
    track("login_walkthrough_view", { slide: WAYPOINTS[active].key, index: active });
  }, [active]);

  return (
    <div className="login">
      <Canopy active={active} reduced={reduced} />
      <HudStrip reduced={reduced} />

      <div className="login-left">
        <div className="login-card">
          {/* The mark in a HUD reticle: the card is the boresight of the page. */}
          <div className="login-mark">
            <span className="login-reticle" aria-hidden="true" />
            <BrandMark size={44} />
          </div>
          <h1 className="login-title">
            Data <em>Pilot</em>
          </h1>
          <p className="login-tagline">Your data, flown right.</p>
          {authMode === "google" ? (
            <>
              <div className="login-div">sign in</div>
              <div className="users google" ref={btnRef} />
              {error && <p className="error">{error}</p>}
              {/* Preflight lamps carry the guarantees in the cockpit's own
                  clipped voice; the capstone on the story panel spells
                  row-level security out in full for a first-time reader. */}
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
              {error && <p className="error">{error}</p>}
              <div className="annunciators login-preflight">
                <span className="annunciator warn">Dev auth</span>
                <span className="annunciator on">RLS</span>
                <span className="annunciator on">Audited</span>
              </div>
            </>
          )}
        </div>
      </div>

      <Walkthrough active={active} setActive={setActive} setPaused={setPaused} />
    </div>
  );
}

export type { User };
