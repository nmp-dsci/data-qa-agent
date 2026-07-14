// Interactive report editor for the Golden Builder (s14 E1, Goal C).
//
// Renders each golden page through the production PageLayout — the SAME
// visualization chat shows — and lets the curator drag cards to reorder, nudge
// them up/down or across columns, delete them, and switch the page template.
// Every edit calls onChange with new pages, so the golden_report JSON stays in
// sync with what's on screen.
import { useRef } from "react";

import { Page, PageObject, TemplateId } from "../../lib/api";
import { ObjectBody, PageLayout, objectCardClass } from "../../report-engine/PageLayout";

const TEMPLATES: TemplateId[] = ["summary", "insights", "one-col", "two-col", "three-col"];

interface Loc {
  pi: number;
  ci: number;
  oi: number;
}

function locate(pages: Page[], id: string): Loc | null {
  for (let pi = 0; pi < pages.length; pi++) {
    for (let ci = 0; ci < pages[pi].columns.length; ci++) {
      for (let oi = 0; oi < pages[pi].columns[ci].length; oi++) {
        if (pages[pi].columns[ci][oi].element_id === id) return { pi, ci, oi };
      }
    }
  }
  return null;
}

const clone = (pages: Page[]): Page[] => JSON.parse(JSON.stringify(pages)) as Page[];

const ctrl: React.CSSProperties = {
  border: "1px solid rgba(128,128,128,0.4)",
  background: "rgba(128,128,128,0.18)",
  borderRadius: 4,
  fontSize: 11,
  lineHeight: 1.1,
  padding: "1px 5px",
  cursor: "pointer",
};

export function ReportEditor({
  pages,
  onChange,
}: {
  pages: Page[];
  onChange: (pages: Page[]) => void;
}) {
  const dragId = useRef<string | null>(null);

  function setTemplate(pi: number, t: TemplateId) {
    const next = clone(pages);
    next[pi].template = t;
    onChange(next);
  }

  function addPage() {
    onChange([...clone(pages), { template: "one-col", columns: [[]] }]);
  }

  function removePage(pi: number) {
    const next = clone(pages);
    next.splice(pi, 1);
    onChange(next);
  }

  function del(id: string) {
    const loc = locate(pages, id);
    if (!loc) return;
    const next = clone(pages);
    next[loc.pi].columns[loc.ci].splice(loc.oi, 1);
    onChange(next);
  }

  function moveInCol(id: string, dir: -1 | 1) {
    const loc = locate(pages, id);
    if (!loc) return;
    const col = pages[loc.pi].columns[loc.ci];
    const j = loc.oi + dir;
    if (j < 0 || j >= col.length) return;
    const next = clone(pages);
    const c = next[loc.pi].columns[loc.ci];
    [c[loc.oi], c[j]] = [c[j], c[loc.oi]];
    onChange(next);
  }

  function moveCol(id: string, dir: -1 | 1) {
    const loc = locate(pages, id);
    if (!loc) return;
    const cj = loc.ci + dir;
    if (cj < 0 || cj >= pages[loc.pi].columns.length) return;
    const next = clone(pages);
    const [obj] = next[loc.pi].columns[loc.ci].splice(loc.oi, 1);
    next[loc.pi].columns[cj].push(obj);
    onChange(next);
  }

  function onDrop(targetId: string) {
    const src = dragId.current;
    dragId.current = null;
    if (!src || src === targetId) return;
    const s = locate(pages, src);
    if (!s) return;
    const next = clone(pages);
    const [obj] = next[s.pi].columns[s.ci].splice(s.oi, 1);
    const t = locate(next, targetId);
    if (!t) {
      onChange(next);
      return;
    }
    next[t.pi].columns[t.ci].splice(t.oi, 0, obj);
    onChange(next);
  }

  const editableCard = (o: PageObject) => {
    const loc = locate(pages, o.element_id);
    const multiCol = loc ? pages[loc.pi].columns.length > 1 : false;
    return (
      <div
        key={o.element_id}
        className={objectCardClass(o)}
        draggable
        onDragStart={() => {
          dragId.current = o.element_id;
        }}
        onDragOver={(e) => e.preventDefault()}
        onDrop={() => onDrop(o.element_id)}
        style={{ position: "relative" }}
      >
        <div
          style={{
            position: "absolute",
            top: 4,
            right: 4,
            display: "flex",
            gap: 2,
            zIndex: 2,
          }}
        >
          <button type="button" title="move up" style={ctrl} onClick={() => moveInCol(o.element_id, -1)}>
            ↑
          </button>
          <button type="button" title="move down" style={ctrl} onClick={() => moveInCol(o.element_id, 1)}>
            ↓
          </button>
          {multiCol && (
            <button type="button" title="column left" style={ctrl} onClick={() => moveCol(o.element_id, -1)}>
              ◀
            </button>
          )}
          {multiCol && (
            <button type="button" title="column right" style={ctrl} onClick={() => moveCol(o.element_id, 1)}>
              ▶
            </button>
          )}
          <button type="button" title="remove" style={ctrl} onClick={() => del(o.element_id)}>
            ✕
          </button>
        </div>
        <ObjectBody o={o} />
      </div>
    );
  };

  if (pages.length === 0) {
    return (
      <div style={{ fontSize: 13 }}>
        <div style={{ opacity: 0.6, marginBottom: 6 }}>
          No pages yet — “Draft with agent”, run the sandbox and “Add output as page”, or add a blank
          page.
        </div>
        <button type="button" style={{ ...ctrl, padding: "4px 10px" }} onClick={addPage}>
          ＋ Add blank page
        </button>
      </div>
    );
  }

  return (
    <div className="report">
      {pages.map((page, pi) => (
        <div key={pi} style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
            <span style={{ fontSize: 11, opacity: 0.7 }}>page {pi + 1}</span>
            <select
              value={page.template}
              onChange={(e) => setTemplate(pi, e.target.value as TemplateId)}
            >
              {TEMPLATES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <button type="button" style={ctrl} title="remove this page" onClick={() => removePage(pi)}>
              ✕ page
            </button>
            <span style={{ fontSize: 11, opacity: 0.5 }}>
              drag cards to reorder · ↑↓ move · ◀▶ column · ✕ remove
            </span>
          </div>
          <div className="answer-page">
            <PageLayout page={page} renderObject={editableCard} />
          </div>
        </div>
      ))}
      <button type="button" style={{ ...ctrl, padding: "4px 10px" }} onClick={addPage}>
        ＋ Add blank page
      </button>
    </div>
  );
}
