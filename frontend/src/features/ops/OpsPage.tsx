// The Ops flight deck (s32 W0) — "is it healthy, safe, fast and affordable?"
// answered by a glance at a cockpit instead of a grep through CloudWatch.
//
// Everything here renders through primitives the app already owns: the Flight
// Deck kit (HudBox / Annunciator / InstrumentLabel) for the readouts and lamps,
// and report-engine/PageLayout + ui/charts/* for the panels — the same path the
// Explore and Evaluations tabs take, so this tab inherits theming, the chart
// error boundaries and the SQL-link affordance for free. Nothing bespoke.
//
// One read (`/admin/ops/summary`) serves the whole page from a pre-aggregated
// rollup (decision Q3), polled on an interval — the first polling surface in
// the app. Panels with no rows yet say "no data yet"; a cold rollup renders the
// frame and fills in on the next poll rather than blocking on a 3M-row scan.

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getOpsRuns,
  getOpsSummary,
  LampStateName,
  OpsMetrics,
  OpsRun,
  OpsSummary,
  OpsWindow,
  Page,
  PageObject,
  refreshOps,
} from "../../lib/api";
import { PageLayout } from "../../report-engine/PageLayout";
import { Annunciator, Annunciators, HudBox, InstrumentLabel } from "../../ui/flightdeck";

const POLL_MS = 30_000;

// ---------------------------------------------------------------------------
// Formatters — every one of them tolerates null, because "no data yet" is the
// normal state of a panel whose workstream hasn't landed.
// ---------------------------------------------------------------------------

function ms(value: number | null | undefined): string {
  if (value == null) return "—";
  return value >= 10_000 ? `${(value / 1000).toFixed(1)}s` : `${Math.round(value)}ms`;
}

function pct(value: number | null | undefined, digits = 1): string {
  return value == null ? "—" : `${(value * 100).toFixed(digits)}%`;
}

function num(value: number | null | undefined, digits = 0): string {
  return value == null ? "—" : value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function usd(value: number | null | undefined, digits = 4): string {
  return value == null ? "—" : `$${value.toFixed(digits)}`;
}

function age(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  if (seconds < 172_800) return `${(seconds / 3600).toFixed(1)}h`;
  return `${Math.round(seconds / 86_400)}d`;
}

/** A lamp state for a rate where lower is better (errors, degradation). */
function rateLamp(rate: number | null | undefined, warn: number, bad: number): LampStateName {
  if (rate == null) return "off";
  if (rate >= bad) return "bad";
  if (rate >= warn) return "warn";
  return "on";
}

// ---------------------------------------------------------------------------
// Page-object builders — the deck's charts are page objects, so PageLayout and
// the existing renderers do the drawing. Each returns null when its data hasn't
// arrived, and a column of nulls simply renders nothing.
// ---------------------------------------------------------------------------

function obj(
  type: PageObject["type"],
  element_id: string,
  data: Record<string, unknown>,
): PageObject {
  return { type, element_id, data };
}

/** Red-team pass rate per attack class (W3). */
function redteamBars(m: OpsMetrics): PageObject | null {
  const categories = m.security?.latest_run?.by_category;
  if (!categories || Object.keys(categories).length === 0) return null;
  const rows = Object.entries(categories).map(([category, value]) => {
    // The writer may send either a ratio or {passed,total}; accept both so the
    // panel doesn't depend on which recorder produced the row.
    const v = value as number | { passed?: number; total?: number };
    const rate = typeof v === "number" ? v : v.total ? (v.passed ?? 0) / v.total : null;
    return {
      category,
      pass_pct: rate == null ? null : Math.round(rate * 1000) / 10,
    };
  });
  return obj("breakdown", "ops:redteam", {
    title: "Red-team pass by attack class",
    dimension: "category",
    measure: "pass_pct",
    unit: "percent",
    height: "sm",
    rows,
  });
}

/** Errors and degradations by service/surface (W1). */
function errorBars(m: OpsMetrics): PageObject | null {
  const bySource = m.errors?.by_source;
  if (!bySource || Object.keys(bySource).length === 0) return null;
  return obj("breakdown", "ops:errors", {
    title: "Errors & degradations by surface",
    dimension: "surface",
    measure: "count",
    unit: "number",
    height: "sm",
    rows: Object.entries(bySource).map(([surface, count]) => ({
      surface,
      count,
    })),
  });
}

/** Traffic mix across chat / Explore / SQL editor. */
function trafficBars(m: OpsMetrics): PageObject | null {
  const mix = m.traffic?.by_source;
  if (!mix || Object.keys(mix).length === 0) return null;
  return obj("breakdown", "ops:traffic", {
    title: "Runs by surface",
    dimension: "surface",
    measure: "runs",
    unit: "number",
    height: "sm",
    rows: Object.entries(mix).map(([surface, runs]) => ({ surface, runs })),
  });
}

/** The deploy timeline (W4). */
function deployTable(m: OpsMetrics): PageObject | null {
  const deploys = m.deploys ?? [];
  if (deploys.length === 0) return null;
  return obj("table", "ops:deploys", {
    title: "Deploy timeline",
    variant: "plain",
    columns: [
      { key: "sha", label: "sha", align: "left", format: "text" },
      { key: "status", label: "status", align: "left", format: "text" },
      { key: "when", label: "started", align: "left", format: "text" },
      { key: "duration", label: "duration", align: "right", format: "text" },
      { key: "smoke", label: "smoke", align: "left", format: "text" },
    ],
    rows: deploys.map((d) => ({
      sha: d.git_sha || "—",
      status: d.status,
      when: d.started_at ? new Date(d.started_at).toLocaleString() : "—",
      duration: d.duration_s == null ? "—" : `${Math.round(d.duration_s / 60)}m`,
      smoke:
        typeof d.smoke?.["passed"] === "number" && typeof d.smoke?.["total"] === "number"
          ? `${d.smoke["passed"]}/${d.smoke["total"]}`
          : "—",
    })),
  });
}

/** Data freshness — the metric whose absence took Explore down in prod (W2). */
function freshnessTable(m: OpsMetrics): PageObject | null {
  const f = m.freshness;
  if (!f?.available) return null;
  const counts = Object.entries(f.row_counts ?? {})
    .map(([k, v]) => `${k} ${num(Number(v))}`)
    .join(" · ");
  return obj("table", "ops:freshness", {
    title: "Data freshness · pipeline",
    variant: "plain",
    columns: [
      { key: "metric", label: "metric", align: "left", format: "text" },
      { key: "value", label: "value", align: "left", format: "text" },
      { key: "detail", label: "detail", align: "left", format: "text" },
    ],
    rows: [
      {
        metric: "marts age",
        value: age(f.age_s),
        detail: f.created_at ? new Date(f.created_at).toLocaleString() : "—",
      },
      {
        metric: "dbt tests",
        value: f.dbt_total ? `${f.dbt_pass ?? 0} / ${f.dbt_total}` : "—",
        detail: f.duration_s == null ? "—" : `last run ${Math.round(f.duration_s / 60)}m`,
      },
      { metric: "rows", value: counts || "—", detail: f.source ?? "—" },
    ],
  });
}

/** The slowest recent asks, each linking out to its Logfire trace. */
function runsTable(runs: OpsRun[]): PageObject | null {
  if (runs.length === 0) return null;
  return obj("table", "ops:runs", {
    title: "Slowest asks · 7d",
    variant: "ranked",
    bar_key: "latency_ms",
    columns: [
      { key: "question", label: "question", align: "left", format: "text" },
      { key: "latency_ms", label: "answer", align: "right", format: "number" },
      { key: "ttfp", label: "first page", align: "right", format: "text" },
      { key: "status", label: "status", align: "left", format: "text" },
      { key: "cost", label: "cost", align: "right", format: "text" },
      { key: "trace", label: "trace", align: "left", format: "text" },
    ],
    rows: runs.map((r) => ({
      question: r.question,
      latency_ms: r.latency_ms,
      ttfp: ms(r.ttfp_ms),
      status: r.degraded ? "degraded" : r.status,
      cost: r.cost_usd == null ? "—" : usd(r.cost_usd),
      // The id itself, not a link: DataTable renders text. The trace column is
      // the handle you paste into Logfire, and the deep-link buttons below the
      // table open it directly.
      trace: r.otel_trace_id ? r.otel_trace_id.slice(0, 12) : "—",
    })),
  });
}

function pageOf(columns: (PageObject | null)[][], template: Page["template"]): Page | null {
  const kept = columns.map((col) => col.filter((o): o is PageObject => o !== null));
  if (kept.every((col) => col.length === 0)) return null;
  return { template, columns: kept };
}

// ---------------------------------------------------------------------------
// Panels
// ---------------------------------------------------------------------------

function Band({ m }: { m: OpsMetrics }) {
  const availability = m.slo?.availability;
  const responsiveness = m.slo?.responsiveness;
  const freshness = m.freshness;
  const security = m.security?.latest_run;
  const deploy = (m.deploys ?? [])[0];
  const saturation = m.saturation;

  const deployLamp: LampStateName =
    deploy == null
      ? "off"
      : deploy.status === "deployed"
        ? "on"
        : deploy.status === "running"
          ? "warn"
          : "bad";

  return (
    <Annunciators>
      <Annunciator
        state={availability?.state ?? "off"}
        title={`SLO-A: ${pct(availability?.target, 0)} of asks served over ${m.slo?.window ?? "28 days"}`}
      >
        availability {pct(availability?.attained, 2)}
      </Annunciator>
      <Annunciator
        state={responsiveness?.state ?? "off"}
        title={`SLO-B: p95 time-to-first-page under ${ms(responsiveness?.target_ms)}`}
      >
        ttfp p95 {ms(responsiveness?.attained_ms)}
      </Annunciator>
      <Annunciator
        state={rateLamp(availability?.error_budget_burn, 0.5, 1)}
        title="Share of the 28-day error budget spent; over 100% means the objective was missed"
      >
        err budget{" "}
        {availability?.error_budget_burn == null
          ? "—"
          : `${Math.round(availability.error_budget_burn * 100)}%`}
      </Annunciator>
      <Annunciator
        state={saturation?.available ? "on" : "off"}
        title={
          saturation?.available
            ? `App Runner + Aurora metrics pulled ${saturation.fetched_at ?? ""}`
            : `Tier-2 saturation ${saturation?.reason ?? "unavailable"} — Tier-1 telemetry unaffected`
        }
      >
        saturation {saturation?.available ? "ok" : "n/a"}
      </Annunciator>
      <Annunciator
        state={freshness?.state ?? "off"}
        title="Age of the marts since the last pipeline run"
      >
        marts {age(freshness?.age_s)}
      </Annunciator>
      <Annunciator
        state={rateLamp(m.errors?.error_rate, 0.01, 0.05)}
        title="Share of runs that errored or degraded"
      >
        errors {pct(m.errors?.error_rate, 2)}
      </Annunciator>
      <Annunciator
        state={security == null ? "off" : (security.pass_rate ?? 0) >= 0.95 ? "on" : "warn"}
        title={
          security
            ? `${security.kind} pack: ${security.passed}/${security.total} passed`
            : "No security pack has run yet"
        }
      >
        redteam {pct(security?.pass_rate, 0)}
      </Annunciator>
      <Annunciator
        state={deployLamp}
        title={deploy ? `${deploy.status} · ${deploy.git_sha}` : "No deploy recorded"}
      >
        last deploy {deploy ? deploy.status : "—"}
      </Annunciator>
      <Annunciator state="on" title="Every read on this deck runs under RLS and is audited">
        rls · audit
      </Annunciator>
    </Annunciators>
  );
}

function Telemetry({ m }: { m: OpsMetrics }) {
  const l = m.latency;
  const e = m.errors;
  const c = m.cost;
  return (
    <div className="ops-grid">
      <HudBox label="full answer p95" value={ms(l?.answer_p95_ms)}>
        <Sub>extract-bound · an eval lever</Sub>
      </HudBox>
      <HudBox label="time to first page p95" value={ms(l?.ttfp_p95_ms)} lit>
        <Sub>the felt latency</Sub>
      </HudBox>
      <HudBox label="error / degraded" value={pct(e?.error_rate, 2)}>
        <Sub>
          {num(e?.degraded)} degraded / {num(e?.runs)} runs
        </Sub>
      </HudBox>
      <HudBox label="cost / answer" value={usd(c?.per_answer_usd)}>
        <Sub>cache-adjusted · {pct(c?.cache_hit_ratio, 0)} cache hit</Sub>
      </HudBox>
    </div>
  );
}

function Sub({ children }: { children: React.ReactNode }) {
  return <div className="ops-tile-sub">{children}</div>;
}

function Saturation({ m }: { m: OpsMetrics }) {
  const s = m.saturation;
  const cap = s?.limits?.max_concurrency;
  return (
    <div className="ops-grid">
      <HudBox
        label="backend cpu / mem"
        value={
          s?.available
            ? `${pct((s.backend?.cpu_pct ?? 0) / 100, 0)} / ${pct((s.backend?.mem_pct ?? 0) / 100, 0)}`
            : "—"
        }
      >
        <Sub>App Runner · {num(s?.backend?.instances)} instance(s)</Sub>
      </HudBox>
      <HudBox
        label="agent cpu / mem"
        value={
          s?.available
            ? `${pct((s.agent?.cpu_pct ?? 0) / 100, 0)} / ${pct((s.agent?.mem_pct ?? 0) / 100, 0)}`
            : "—"
        }
      >
        <Sub>App Runner · {num(s?.agent?.instances)} instance(s)</Sub>
      </HudBox>
      <HudBox
        label="aurora acu / conns"
        value={s?.available ? `${num(s.aurora?.acu, 2)} / ${num(s.aurora?.connections)}` : "—"}
      >
        <Sub>cold starts {num(m.reliability?.db_cold_starts)} this window</Sub>
      </HudBox>
      <HudBox label="concurrency cap" value={cap == null ? "—" : num(cap)}>
        <Sub>cap hits {num(m.security?.cap_hits)} · daily LLM limit</Sub>
      </HudBox>
    </div>
  );
}

function Product({ m }: { m: OpsMetrics }) {
  const t = m.traffic;
  const p = m.product;
  const c = m.cost;
  const j = m.judge;
  const load = m.reliability?.latest_load_test;
  return (
    <div className="ops-grid">
      <HudBox label="asks / active users" value={`${num(t?.asks)} / ${num(t?.active_users)}`}>
        <Sub>{num(t?.asks_per_user, 1)} asks per user</Sub>
      </HudBox>
      <HudBox label="thumbs-up rate" value={pct(p?.thumbs_up_rate, 0)}>
        <Sub>
          {num(p?.thumbs_up)} up · {num(p?.thumbs_down)} down
        </Sub>
      </HudBox>
      <HudBox label="no-answer rate" value={pct(m.errors?.no_answer_rate, 1)}>
        <Sub>{num(m.errors?.no_answer)} honest refusals</Sub>
      </HudBox>
      <HudBox label="spend / budget" value={`${usd(c?.total_usd, 2)} / ${usd(c?.budget_usd, 0)}`}>
        <Sub>
          {num(c?.priced_asks)} of {num(t?.asks)} asks priced
        </Sub>
      </HudBox>
      <HudBox
        label="live judge"
        value={j?.insight_mean == null ? "—" : `${num(j.insight_mean, 1)}/10`}
      >
        <Sub>{num(j?.sampled)} sampled asks</Sub>
      </HudBox>
      <HudBox label="load p95" value={load ? ms(load.p95_ms) : "—"}>
        <Sub>
          {load
            ? `${load.scenario || "chat"} · ${num(load.vus)} vu · ${pct(load.error_rate, 2)} err`
            : "no load test yet"}
        </Sub>
      </HudBox>
      <HudBox label="retries / attempts" value={num(m.reliability?.attempts_mean, 2)}>
        <Sub>{num(m.reliability?.retried)} asks needed a retry</Sub>
      </HudBox>
      <HudBox
        label="denials · auth fails"
        value={`${num(m.security?.denials)} · ${num(m.security?.auth_failures)}`}
      >
        <Sub>guard rejections · failed sign-ins</Sub>
      </HudBox>
    </div>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="ops-section">
      <div className="ops-section-label">
        <InstrumentLabel tone="dim">{label}</InstrumentLabel>
      </div>
      {children}
    </section>
  );
}

function TraceLinks({ runs }: { runs: OpsRun[] }) {
  const traced = runs.filter((r) => r.otel_trace_id);
  if (traced.length === 0) return null;
  return (
    <p className="ops-note">
      {traced.length} of {runs.length} slow asks carry a Logfire trace id — the deck is the outcomes
      plane, Logfire the microscope. Search the id in your Logfire project to open the span
      waterfall.
    </p>
  );
}

// ---------------------------------------------------------------------------
// The tab
// ---------------------------------------------------------------------------

export function OpsPage() {
  const [window_, setWindow] = useState<OpsWindow>("24h");
  const [summary, setSummary] = useState<OpsSummary | null>(null);
  const [runs, setRuns] = useState<OpsRun[]>([]);
  const [msg, setMsg] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (w: OpsWindow) => {
    try {
      const [s, r] = await Promise.all([getOpsSummary(w), getOpsRuns(20)]);
      setSummary(s);
      setRuns(r);
      setMsg("");
    } catch (e) {
      setMsg((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void load(window_);
  }, [load, window_]);

  // The first polling surface in the app: a deck whose numbers are a poll old is
  // a deck you stop trusting. The interval is deliberately slower than the
  // rollup's staleness threshold, so a poll finds fresh numbers rather than
  // repeatedly triggering the same refresh.
  useEffect(() => {
    const timer = globalThis.setInterval(() => void load(window_), POLL_MS);
    return () => globalThis.clearInterval(timer);
  }, [load, window_]);

  const m = summary?.metrics ?? {};

  const securityPage = useMemo(
    () => pageOf([[redteamBars(m)], [errorBars(m)]], "two-col"),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [summary],
  );
  const deliveryPage = useMemo(
    () => pageOf([[deployTable(m)], [freshnessTable(m), trafficBars(m)]], "two-col"),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [summary],
  );
  const runsPage = useMemo(() => pageOf([[runsTable(runs)]], "one-col"), [runs]);

  async function doRefresh() {
    setRefreshing(true);
    try {
      await refreshOps();
      await load(window_);
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <main className="ops" aria-label="Operations">
      <header className="ops-head">
        <div>
          <InstrumentLabel tone="accent">data pilot · ops</InstrumentLabel>
          <div className="ops-sub">
            {summary?.refreshed_at
              ? `rollup refreshed ${age(summary.age_s)} ago`
              : "rollup not built yet — refreshing"}
            {summary?.stale && " · stale, refreshing in the background"}
          </div>
        </div>
        <div className="ops-controls">
          {(["24h", "7d", "28d"] as OpsWindow[]).map((w) => (
            <button
              key={w}
              onClick={() => setWindow(w)}
              aria-pressed={window_ === w}
              className={window_ === w ? "chip active" : "chip"}
            >
              {w}
            </button>
          ))}
          <button onClick={() => void doRefresh()} disabled={refreshing} className="chip">
            {refreshing ? "refreshing…" : "refresh"}
          </button>
        </div>
      </header>

      {msg && <p className="error">{msg}</p>}

      <div className="ops-band">
        <Band m={m} />
      </div>

      <Section label={`telemetry · ${window_}`}>
        <Telemetry m={m} />
      </Section>

      <Section label="saturation & dependency health">
        <Saturation m={m} />
        {!m.saturation?.available && (
          <p className="ops-note">
            Tier-2 infra metrics are {m.saturation?.reason ?? "unavailable"}. Everything else on
            this deck is Postgres-native and unaffected.
          </p>
        )}
      </Section>

      <Section label={`traffic, product & quality · ${window_}`}>
        <Product m={m} />
      </Section>

      {securityPage && (
        <Section label="security & errors">
          <PageLayout page={securityPage} />
        </Section>
      )}

      {deliveryPage && (
        <Section label="delivery & data">
          <PageLayout page={deliveryPage} />
        </Section>
      )}

      {runsPage && (
        <Section label="slowest asks">
          <PageLayout page={runsPage} />
          <TraceLinks runs={runs} />
        </Section>
      )}

      {summary && !summary.refreshed_at && (
        <p className="muted ops-section">
          No rollup yet. The first read triggers one in the background — this page fills in on the
          next poll, or press <strong>refresh</strong> to wait for it.
        </p>
      )}
    </main>
  );
}
