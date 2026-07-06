# The Insight Playbook — knowledge tree + learning loop

This folder is the agent's know-how, as versioned markdown. It replaces the giant
system-prompt string: the agent greps these pages at query time and loads only
what's relevant, and the same files are what a coding agent edits to "teach" it.

## Layout
- `presentation/` — HOW to present (report structure, what makes an insight, chart
  conventions). Cross-domain.
- `analysis/` — HOW to compute (growth off a 12-month rolling base, rolling-average
  window choice, latest-reliable month, yield). Cross-domain.
- `domains/<dataset>/` — WHAT each dataset means (grain, columns, gotchas). One
  folder per dataset; copy `domains/_template/` to add a new one (demographics,
  stock prices, …) without touching the rest.
- `INDEX.md` — auto-generated map (name · description per page), pinned in the
  system prompt. Regenerate with `python -m agent.knowledge`.

Each page has frontmatter: `name`, `description` (used in the index + search
ranking), and `applies_to` (question terms that should surface the page).

## How the agent uses it (K1–K2)
1. `search_knowledge(query)` — ripgrep-style ranked search (an agent tool).
2. `read_knowledge(name)` — load a full page.
3. The agent plans the report, runs SQL (kept + numbered), computes headline maths
   with the deterministic `analytics` tools, builds charts, and returns a typed
   `InsightReport`.

`knowledge_version()` (a content hash of this tree) is stamped on every report so
feedback can tell which knowledge produced an answer.

## The learning loop (K5–K6)
1. Users leave element-anchored feedback on a report (click-to-annotate).
2. Admins triage it (`/admin` → Feedback): **promote** to an eval case, save as
   **user memory**, or **dismiss**. Promoted cases live in `app.eval_cases`
   (DB-backed, status toggleable stale↔active, auto-archive after 3 stale cycles).
3. `POST /admin/eval-cases/run-staleness` re-checks cases and flags stale ones.

The write step that turns those signals into edits is deliberately left loose for
now: after the sandbox+skills restructure the unit of improvement is the tested
**skill** (`agent/skills/`), not a knowledge page, so a knowledge-page curator no
longer fits. A human reads the promoted eval cases and edits the relevant skill or
domain page directly. (The old `evals/curator.py` proposal generator was removed.)

## Hygiene
`python -m agent.knowledge --lint` checks for broken `[[cross-links]]`, missing
descriptions, and duplicate names. Run it before committing knowledge edits.
