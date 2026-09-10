// s38 P2.5 · Visitor analytics — first-party, admin-only.
//
// The demo's whole measurement story runs on the app's own event log: the
// beacon in lib/api.ts stamps every event with an anonymous visitor_id, and
// this tab reads the rollups from /analytics/summary. Nothing here is visible
// to demo visitors (adminStrict in the nav; require_admin on the endpoint) —
// they are the subject of the data, never its audience.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { AnalyticsSummary, getAnalyticsSummary, getHandoverAnalytics } from "../../lib/api";
import { HudBox } from "../../ui/flightdeck";
import { SimpleTable } from "../../ui/SimpleTable";

const WINDOWS = [7, 14, 30] as const;

function pctOf(n: number, of: number): string {
  if (!of) return "—";
  return `${Math.round((n / of) * 100)}%`;
}

function shortDay(iso: string): string {
  return iso.slice(5); // MM-DD
}

function timeAgo(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 90) return `${Math.round(s)}s ago`;
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  if (s < 129600) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function Funnel({ data }: { data: AnalyticsSummary["funnel"] }) {
  const max = Math.max(1, ...data.map((d) => d.visitors));
  return (
    <div className="ana-funnel">
      {data.map((stage) => (
        <div key={stage.event} className="ana-funnel-row">
          <span className="ana-funnel-label">{stage.label}</span>
          <span className="ana-funnel-track">
            <span className="ana-funnel-bar" style={{ width: `${(stage.visitors / max) * 100}%` }} />
          </span>
          <span className="ana-funnel-n">{stage.visitors}</span>
        </div>
      ))}
    </div>
  );
}

function Daily({ data }: { data: AnalyticsSummary["daily"] }) {
  const max = Math.max(1, ...data.map((d) => d.events));
  return (
    <div className="ana-daily" role="img" aria-label="Events per day">
      {data.map((d) => (
        <div key={d.day} className="ana-day" title={`${d.day}: ${d.events} events · ${d.visitors} visitors`}>
          <span className="ana-day-bar" style={{ height: `${Math.max(4, (d.events / max) * 100)}%` }} />
          <span className="ana-day-label">{shortDay(d.day)}</span>
        </div>
      ))}
    </div>
  );
}

export function AnalyticsPage() {
  const [days, setDays] = useState<(typeof WINDOWS)[number]>(14);
  const { data, isLoading, error } = useQuery({
    queryKey: ["analytics", days],
    queryFn: () => getAnalyticsSummary(days),
    refetchInterval: 60_000,
  });

  if (isLoading) {
    return (
      <main className="ops" aria-busy="true">
        <div className="skel" style={{ height: 40, marginBottom: 10 }} />
        <div className="skel" style={{ height: 280 }} />
      </main>
    );
  }
  if (error || !data) {
    return (
      <main className="ops">
        <p className="error">Could not load analytics: {(error as Error)?.message}</p>
      </main>
    );
  }
  const t = data.totals;
  return (
    <main className="ops" aria-label="Analytics">
      <header className="ops-head">
        <div>
          <h1>Visitor analytics</h1>
          <div className="ops-sub">
            first-party · anonymous visitor ids · no third-party trackers
          </div>
        </div>
        <div className="ops-controls">
          {WINDOWS.map((w) => (
            <button
              key={w}
              className={days === w ? "chip active" : "chip"}
              onClick={() => setDays(w)}
            >
              {w}d
            </button>
          ))}
        </div>
      </header>

      <div className="ops-grid">
        <HudBox label={`visitors · ${data.days}d`} value={t.visitors} lit>
          <div className="ops-tile-sub">{t.today_visitors} today</div>
        </HudBox>
        <HudBox label="returning" value={t.returning_visitors}>
          <div className="ops-tile-sub">{pctOf(t.returning_visitors, t.visitors)} of visitors · seen on &gt;1 day</div>
        </HudBox>
        <HudBox label="sessions" value={t.sessions}>
          <div className="ops-tile-sub">browser tabs opened</div>
        </HudBox>
        <HudBox label="events" value={t.events}>
          <div className="ops-tile-sub">all surfaces</div>
        </HudBox>
      </div>

      <section className="ops-section">
        <div className="ops-section-label">Funnel — how far visitors get</div>
        <Funnel data={data.funnel} />
        <p className="ops-note">
          Distinct visitors reaching each stage. The gap between "asked a question" and the
          outbound click is the demo's story working (or not).
        </p>
      </section>

      <section className="ops-section">
        <div className="ops-section-label">Activity — events per day</div>
        <Daily data={data.daily} />
      </section>

      <section className="ops-section">
        <div className="ops-section-label">Top questions asked</div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Question</th>
                <th>Times</th>
                <th>Engine</th>
              </tr>
            </thead>
            <tbody>
              {data.top_questions.map((q) => (
                <tr key={q.question}>
                  <td>{q.question}</td>
                  <td>{q.count}</td>
                  <td>{q.engine ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="ops-note">
          Free-text questions that missed the recorded pack are the recording backlog: ask them
          in dev, export, and they become chips.
        </p>
      </section>

      <section className="ops-section">
        <div className="ops-section-label">Recent sessions</div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Session</th>
                <th>Last seen</th>
                <th>Events</th>
                <th>Did</th>
              </tr>
            </thead>
            <tbody>
              {data.recent_sessions.map((sess) => (
                <tr key={sess.session_id}>
                  <td className="mono">{sess.session_id.slice(0, 8)}</td>
                  <td>{timeAgo(sess.last_seen)}</td>
                  <td>{sess.events}</td>
                  <td className="ana-etypes">{sess.event_types}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <HandoverSection days={days} />
    </main>
  );
}

// s48 §7 — what happened to a deck after we handed it over. A separate query
// (own window control off — it follows the page's days selector, but degrades
// independently: no decks yet is a normal empty state, not a page error).
function minutesLabel(mins: number | null): string {
  if (mins == null) return "—";
  if (mins < 60) return `${Math.round(mins)}m`;
  if (mins < 1440) return `${Math.round(mins / 60)}h`;
  return `${Math.round(mins / 1440)}d`;
}

function HandoverSection({ days }: { days: number }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["analytics-handover", days],
    queryFn: () => getHandoverAnalytics(days),
    refetchInterval: 60_000,
  });

  return (
    <section className="ops-section">
      <div className="ops-section-label">Handover — decks after they leave the app</div>
      {isLoading && <div className="skel" style={{ height: 120 }} />}
      {error && !isLoading && (
        <p className="error">Could not load handover analytics: {(error as Error).message}</p>
      )}
      {data && !data.decks && (
        <p className="ops-note">
          No decks in the last {data.days} days yet — ask a question that produces one, then give
          the poller (<code>make handover-poll</code>) a pass.
        </p>
      )}
      {data && data.decks > 0 && (
        <>
          <div className="ops-grid">
            <HudBox label={`decks · ${data.days}d`} value={data.decks} lit>
              <div className="ops-tile-sub">handed over as a Slides deck</div>
            </HudBox>
            <HudBox label="opened" value={data.opened}>
              <div className="ops-tile-sub">{pctOf(data.opened, data.decks)} of decks</div>
            </HudBox>
            <HudBox label="edited" value={data.edited}>
              <div className="ops-tile-sub">
                {data.edit_rate != null ? `${Math.round(data.edit_rate * 100)}%` : "—"} edit rate
              </div>
            </HudBox>
            <HudBox label="time to first edit" value={minutesLabel(data.median_minutes_to_first_edit)}>
              <div className="ops-tile-sub">median, among edited decks</div>
            </HudBox>
          </div>

          <div className="ops-section" style={{ marginTop: 16 }}>
            <div className="ops-section-label">Edits by layout</div>
            <SimpleTable
              columns={[
                { key: "layout_id", label: "Layout" },
                { key: "decks", label: "Decks", align: "right" },
                { key: "edits", label: "Edits", align: "right" },
                { key: "headline_edits", label: "Headline edits", align: "right" },
                { key: "chart_edits", label: "Chart edits", align: "right" },
              ]}
              rows={data.edits_by_layout}
            />
          </div>

          <div className="ops-section" style={{ marginTop: 16 }}>
            <div className="ops-section-label">Edits by event</div>
            <SimpleTable
              columns={[
                { key: "event", label: "Event" },
                { key: "edits", label: "Edits", align: "right" },
              ]}
              rows={data.edits_by_event}
            />
          </div>

          <div className="ops-section" style={{ marginTop: 16 }}>
            <div className="ops-section-label">Recent decks</div>
            {/* Hand-rolled, not SimpleTable: SimpleTable stringifies every cell, and
                this is the one table that needs an actual link, not text. */}
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Question</th>
                    <th>Deck</th>
                    <th>Edits</th>
                    <th>Last edit</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent.map((r) => (
                    <tr key={r.run_id}>
                      <td>{r.question ?? "—"}</td>
                      <td>
                        {r.deck_url ? (
                          <a href={r.deck_url} target="_blank" rel="noreferrer">
                            open deck
                          </a>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td>{r.edits}</td>
                      <td>{r.last_edit_at ? timeAgo(r.last_edit_at) : "never"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
