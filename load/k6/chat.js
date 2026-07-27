// The first load numbers this app has ever had (s32 W1).
//
// The audit's blunt finding: no load test had ever been run, so every latency
// claim was a single-user anecdote. This measures the two things that actually
// matter for a served LLM app and are cheap to measure honestly:
//
//   * `chat` — the real /ask path under concurrency. Expensive (each iteration
//     spends model tokens), so it runs at low VU counts for a short duration and
//     is the scenario you run deliberately, not in CI.
//   * `browse` — the read paths (auth, conversations, Explore datasets) at
//     higher concurrency. Free, and the one that finds connection-pool and
//     Aurora-capacity limits, which is where a scale-to-zero database bites.
//
// Deliberately NOT a stress test to failure: this stack is one App Runner
// instance per service with max_concurrency 100 and an Aurora Serverless
// minimum of 0 ACU. Driving it to saturation measures the cost decision, not a
// defect. What we want is a p95 at a realistic concurrency, and confirmation
// that nothing errors when several people ask at once.
//
// Usage (needs a running stack; k6 is not a repo dependency):
//   make loadtest                       # browse, 20 VUs, 30s, local
//   make loadtest SCENARIO=chat VUS=3 DURATION=60s
//   make loadtest BASE_URL=https://<prod-api> TOKEN=<bearer>
//
// Results are written to app.load_tests by scripts/record_load_test.py, which
// reads the summary JSON k6 writes — so the ops deck's load tile shows the last
// real measurement rather than a number from a README.

import http from "k6/http";
import { check, sleep } from "k6";
import { Trend } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const SCENARIO = __ENV.SCENARIO || "browse";
const VUS = parseInt(__ENV.VUS || "20", 10);
const DURATION = __ENV.DURATION || "30s";
// A bearer token for a seeded user. Locally the dev-auth stub mints one (see
// below); against a real deployment pass TOKEN explicitly, because Google
// ID tokens can't be minted from a load script.
const TOKEN = __ENV.TOKEN || "";
const DEV_USER = __ENV.DEV_USER || "user1";

// Named separately from http_req_duration so the chat path's latency isn't
// averaged together with cheap reads — the whole point is to keep them apart.
const askDuration = new Trend("ask_duration", true);
const browseDuration = new Trend("browse_duration", true);

export const options = {
  vus: VUS,
  duration: DURATION,
  // Thresholds are recorded, not enforced as a gate: the first run's job is to
  // establish what "normal" is. Once there is a baseline in app.load_tests, the
  // numbers here become a real budget.
  thresholds: {
    http_req_failed: ["rate<0.05"],
  },
  summaryTrendStats: ["min", "med", "p(90)", "p(95)", "p(99)", "max", "avg"],
};

const QUESTIONS = [
  "What are the top growth suburbs for sale price and rent?",
  "Which suburbs have the highest rent growth?",
  "Show the sale price trend for houses in Hornsby",
  "Top suburbs by sale price growth?",
];

/** A bearer token: the provided one, or a dev-auth login when running locally. */
function token() {
  if (TOKEN) return TOKEN;
  const resp = http.post(
    `${BASE_URL}/auth/dev-login`,
    JSON.stringify({ username: DEV_USER }),
    { headers: { "Content-Type": "application/json" } },
  );
  if (resp.status !== 200) return "";
  try {
    return JSON.parse(resp.body).access_token || "";
  } catch {
    return "";
  }
}

export function setup() {
  const bearer = token();
  if (!bearer) {
    // Fail loudly in setup rather than reporting a run of 401s as "latency".
    throw new Error(
      `could not obtain a token from ${BASE_URL} — pass TOKEN=<bearer> for a deployment ` +
        "with AUTH_MODE=google",
    );
  }
  return { bearer };
}

function headers(data) {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${data.bearer}`,
    // Every load iteration is auditable as load, never confused with real usage
    // (app.query_runs.channel), so a load run can't pollute the ops deck's
    // product metrics.
    "X-Client-Channel": "load",
  };
}

function browse(data) {
  const h = headers(data);
  const me = http.get(`${BASE_URL}/me`, { headers: h });
  check(me, { "me 200": (r) => r.status === 200 });
  browseDuration.add(me.timings.duration);

  const conversations = http.get(`${BASE_URL}/conversations`, { headers: h });
  check(conversations, { "conversations 200": (r) => r.status === 200 });
  browseDuration.add(conversations.timings.duration);

  const datasets = http.get(`${BASE_URL}/explore/datasets`, { headers: h });
  check(datasets, { "datasets 200": (r) => r.status === 200 });
  browseDuration.add(datasets.timings.duration);

  sleep(1);
}

function chat(data) {
  const question = QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)];
  const resp = http.post(
    `${BASE_URL}/ask`,
    JSON.stringify({ question, conversation_id: null }),
    // A full report legitimately runs minutes; the client ceiling is 240s.
    { headers: headers(data), timeout: "240s" },
  );
  check(resp, {
    // 429 is the daily LLM cap doing its job, not a failure of the service —
    // counted as a pass so a capped run doesn't read as an outage.
    "ask answered or capped": (r) => r.status === 200 || r.status === 429,
  });
  askDuration.add(resp.timings.duration);
  sleep(2);
}

export default function (data) {
  if (SCENARIO === "chat") chat(data);
  else browse(data);
}
