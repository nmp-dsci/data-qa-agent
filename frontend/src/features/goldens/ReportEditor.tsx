// Interactive report editor for the Golden Builder (s14 E1, Goal C).
//
// The curator fully composes the golden's presentation here: add / remove
// objects, move them to ANY column (or reorder within one), edit their text
// fields, and add / remove / re-template pages. Edits mutate a working
// DRAFT (the parent's pendingPages) — nothing is committed until the curator
// presses Submit in the Report stage, which writes golden_report + reconciles
// the sandbox output JSON (see GoldensPage).
//
// Unlike the production PageLayout (which collapses empty columns and slices to
// the template width), the editor renders EXACTLY the template's columns —
// including empty ones — as stable drop targets, so "move object to column 1/3"
// behaves predictably and nothing silently vanishes.
//
// An object's DATA is not edited here (s34). A card's edit panel offers only the
// linked sandbox object, the Structured Builder, where it sits (page / column /
// height) and its text. Chart encodings, x-axis sorting and retyping used to live
// here too, which meant two places could change what a chart showed and they
// disagreed; the builder is now the single one.
import { KitSelect } from "@/components/kit/KitSelect";
import { ReactNode, useEffect, useRef, useState } from "react";

import { BuilderObjectType, Page, PageObject, PageObjectType, TemplateId } from "../../lib/api";
import { ObjectBody, objectCardClass } from "../../report-engine/PageLayout";
import {
  OBJECT_TYPE_DESCRIPTIONS,
  OBJECT_TYPE_LABELS,
  TEMPLATES,
  columnTracks,
} from "../../report-engine/registry";

const TEMPLATE_IDS: TemplateId[] = ["one-col", "two-col", "three-col"];
// Derived from the registry's Record<PageObjectType, ...> so a newly added
// object type can't silently miss the picker — TS won't compile
// OBJECT_TYPE_LABELS without every PageObjectType key.
const OBJECT_TYPES: PageObjectType[] = Object.keys(OBJECT_TYPE_LABELS) as PageObjectType[];
const HEIGHTS = ["sm", "md", "lg", "fill"] as const;

// Glyph per object type for the visual add-object picker — 16-viewBox line icons
// matching the app's icon language, keyed 1:1 with the registry object types.
const g = (children: ReactNode) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    {children}
  </svg>
);
const OBJECT_GLYPHS: Record<PageObjectType, ReactNode> = {
  kpi: g(
    <>
      <rect x="2.5" y="3.5" width="11" height="9" rx="1.5" />
      <path d="M5 7.5h6M5 10h3.5" />
    </>,
  ),
  trend: g(<polyline points="2,12 6,7 9,9 14,3" />),
  breakdown: g(<path d="M3 13V8M8 13V4M13 13V10" />),
  compare: g(
    <>
      <path d="M3 13V9M7.5 13V6M12 13V10" />
      <polyline points="2.5,7 7.5,5 13.5,3" />
    </>,
  ),
  insight: g(
    <>
      <circle cx="8" cy="6.5" r="3.6" />
      <path d="M6.5 11.5h3M7 13.5h2" />
    </>,
  ),
  text: g(<path d="M3 4.5h10M3 8h10M3 11.5h6" />),
  table: g(
    <>
      <rect x="2.5" y="3.5" width="11" height="9" rx="1" />
      <path d="M2.5 6.5h11M6 3.5v9" />
    </>,
  ),
  choropleth: g(
    <>
      <path d="M8 2.5c2.5 0 4 1.6 4 3.9C12 9.2 8 13.5 8 13.5S4 9.2 4 6.4C4 4.1 5.5 2.5 8 2.5z" />
      <circle cx="8" cy="6.3" r="1.3" />
    </>,
  ),
};

// A cross-tab: the table glyph plus a second column rule and a header band, so it
// reads as "rows x columns" rather than a plain list.
const PIVOT_GLYPH = g(
  <>
    <rect x="2.5" y="3.5" width="11" height="9" rx="1" />
    <path d="M2.5 6.5h11M6 3.5v9M10 6.5v6" />
  </>,
);

/** A short label for a ② Sandbox output in the link picker (title/label/heading). */
function objectTitle(o: PageObject): string {
  const d = o.data ?? {};
  const raw = d["title"] ?? d["label"] ?? d["heading"] ?? d["text"] ?? "";
  const s = String(raw).trim();
  return s.length > 40 ? `${s.slice(0, 40)}…` : s;
}

const colCount = (t: TemplateId): number => TEMPLATES[t]?.tracks.length ?? 1;

/** Legacy pages used `summary`/`insights` templates (now removed) — render them
 *  as two-col so old goldens stay editable. */
function normTemplate(t: string): TemplateId {
  return (TEMPLATE_IDS as string[]).includes(t) ? (t as TemplateId) : "two-col";
}

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

/** Ensure a page has exactly colCount(template) columns: pad with empties and
 *  fold any overflow (from a narrower template) into the last kept column. */
function normColumns(page: Page): Page {
  const n = colCount(normTemplate(page.template));
  const cols = (page.columns ?? []).map((c) => [...c]);
  while (cols.length < n) cols.push([]);
  if (cols.length > n) {
    const overflow = cols.splice(n);
    for (const extra of overflow) cols[n - 1].push(...extra);
  }
  return { ...page, template: normTemplate(page.template), columns: cols };
}

/** Current relative width per column — from the page's `widths` override, else
 *  parsed from the template's default fr tracks so the inputs seed sensibly. */
function columnWeights(page: Page): number[] {
  const t = normTemplate(page.template);
  const tracks = TEMPLATES[t]?.tracks ?? [];
  return Array.from({ length: colCount(t) }, (_, i) => {
    const w = page.widths?.[i];
    if (typeof w === "number" && Number.isFinite(w) && w > 0) return w;
    const m = /([\d.]+)fr/.exec(tracks[i] ?? "");
    return m ? Number(m[1]) : 1;
  });
}

const defaultData = (type: PageObjectType): Record<string, unknown> => {
  switch (type) {
    case "kpi":
      return { label: "New KPI", value: "", basis: "" };
    case "trend":
      return { title: "", x: "month", y: "value", series: null, rows: [], height: "md" };
    case "breakdown":
      return { title: "", dimension: "", measure: "", rows: [], height: "md" };
    case "compare":
      return { title: "", dimension: "", measure: "", group: null, rows: [], height: "md" };
    case "insight":
      return { heading: "New insight", text: "", refs: [] };
    case "table":
      return { title: "", variant: "plain", columns: [], rows: [] };
    case "choropleth":
      return { title: "", layer: "poa_nsw", key_field: "postcode", value_field: "value", rows: [], height: "md" };
    default:
      return { text: "New note" };
  }
};

const newObject = (type: PageObjectType): PageObject => ({
  type,
  element_id: `edit:${type}:${Date.now().toString(36)}:${Math.random().toString(36).slice(2, 6)}`,
  role: null,
  data: defaultData(type),
});

/** Text fields the curator may edit per type (series stay as the sandbox made
 *  them). Each entry is [data key, label, multiline?]. */
const TEXT_FIELDS: Record<PageObjectType, [string, string, boolean][]> = {
  kpi: [
    ["label", "label", false],
    ["value", "value", false],
    ["basis", "basis", false],
  ],
  trend: [["title", "title", false]],
  breakdown: [["title", "title", false]],
  compare: [["title", "title", false]],
  insight: [
    ["heading", "heading", false],
    ["text", "text", true],
  ],
  text: [["text", "text", true]],
  table: [["title", "title", false]],
  choropleth: [["title", "title", false]],
};

const ctrl: React.CSSProperties = {
  border: "1px solid rgba(128,128,128,0.4)",
  background: "rgba(128,128,128,0.18)",
  // Without an explicit color a <button> inherits the UA default (black), which is
  // illegible on this dark control background — the report editor's ↑↓✎✕ / ✕ page /
  // Add-page controls all use this style, so pin them to the theme text token.
  color: "var(--text)",
  borderRadius: 4,
  fontSize: 11,
  lineHeight: 1.1,
  padding: "1px 5px",
  cursor: "pointer",
};
const label: React.CSSProperties = {
  fontSize: 10,
  textTransform: "uppercase",
  letterSpacing: 0.5,
  opacity: 0.6,
};
const fieldInput: React.CSSProperties = {
  fontSize: 12,
  padding: "3px 5px",
  width: "100%",
  fontFamily: "inherit",
  boxSizing: "border-box",
};

export function ReportEditor({
  pages,
  onChange,
  onEditInBuilder,
  sandboxObjects = [],
  builderPanel,
  editingId,
  onCloseBuilder,
}: {
  pages: Page[];
  onChange: (pages: Page[]) => void;
  /** The Structured Builder, to render under the object currently being edited
   *  so the curator stays where they are. Rendered only for `editingId`. */
  builderPanel?: React.ReactNode;
  editingId?: string | null;
  /** Collapse the Structured Builder without leaving the card's edit panel. */
  onCloseBuilder?: () => void;
  /** Edit a chart object in the deterministic Structured Builder (s30): the parent
   *  seeds the builder from the object's stored spec and scrolls to it. Replaces
   *  the old plain-English "describe this object" LLM path. When absent, the
   *  "edit in builder" button is hidden (e.g. a non-golden consumer). */
  onEditInBuilder?: (o: PageObject, builderType?: BuilderObjectType) => void;
  /** The objects the ② Sandbox produced — an object can be *linked* to one of
   *  these (by element_id) to base its data on it (the "linked object" picker). */
  sandboxObjects?: PageObject[];
}) {
  const dragId = useRef<string | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());
  // Keyboard copy / paste / delete on the interactive draft (drag-and-drop is
  // unchanged). `selectedId` is the focused/highlighted card; `clipboard` holds
  // a detached copy of an object; `focusNext` queues the card to focus after a
  // paste/delete re-render so the keyboard flow continues without the mouse.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [clipboard, setClipboard] = useState<PageObject | null>(null);
  const focusNext = useRef<string | null>(null);
  // Which column's visual "add object" picker is open ("pi:ci"), if any.
  const [pickerKey, setPickerKey] = useState<string | null>(null);

  const emit = (next: Page[]) => onChange(next.map(normColumns));

  function toggleOpen(id: string) {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function setTemplate(pi: number, t: TemplateId) {
    const next = clone(pages);
    next[pi].template = t;
    emit(next); // normColumns folds/pads to the new width — nothing is lost
  }

  function setHeadline(pi: number, value: string) {
    const next = clone(pages);
    if (value.trim()) next[pi].headline = value;
    else delete next[pi].headline;
    emit(next);
  }

  function setColumnWidth(pi: number, ci: number, value: number) {
    const next = clone(pages);
    const weights = columnWeights(next[pi]);
    weights[ci] = value;
    next[pi].widths = weights.map((w) => Math.round(w * 100) / 100);
    emit(next);
  }

  function resetWidths(pi: number) {
    const next = clone(pages);
    delete next[pi].widths;
    emit(next);
  }

  function addPage() {
    emit([...clone(pages), { template: "one-col", columns: [[]] }]);
  }

  function removePage(pi: number) {
    const next = clone(pages);
    next.splice(pi, 1);
    emit(next);
  }

  function addObject(pi: number, ci: number, type: PageObjectType, builderType?: BuilderObjectType) {
    const obj = newObject(type);
    // Normalise BEFORE the push, not just on emit: the editor renders one drop
    // target per TEMPLATE column, but a stored page can carry fewer column arrays
    // (a golden saved under a narrower template, or an agent draft). Adding into a
    // column that exists on screen but not in the data threw on `.push` of
    // undefined and silently dropped the click.
    const next = clone(pages).map(normColumns);
    next[pi].columns[ci].push(obj);
    emit(next);
    setPickerKey(null);
    // Drop straight into editing the new object: open its fields panel, select it
    // and queue focus — so "Add object" lands you in edit mode, not a blank card.
    setOpen((prev) => new Set(prev).add(obj.element_id));
    setSelectedId(obj.element_id);
    focusNext.current = obj.element_id;
    // A builder-authored type (pivot) opens the Structured Builder on the new,
    // empty card so the curator configures it where they added it.
    if (builderType) onEditInBuilder?.(obj, builderType);
  }

  /** Add an object linked to a ② Sandbox output — a deep copy that keeps the
   *  sandbox object's type + data + element_id, so the curated card carries real
   *  data (the shared element_id IS the link, same as bindLinked). */
  function addLinked(pi: number, ci: number, src: PageObject) {
    // Same missing-column guard as addObject.
    const next = clone(pages).map(normColumns);
    next[pi].columns[ci].push(JSON.parse(JSON.stringify(src)) as PageObject);
    emit(next);
    setPickerKey(null);
  }

  function del(id: string) {
    const loc = locate(pages, id);
    if (!loc) return;
    const next = clone(pages);
    next[loc.pi].columns[loc.ci].splice(loc.oi, 1);
    emit(next);
  }

  // After a paste/delete, move DOM focus to the queued card so ⌘C/⌘V/Del keep
  // working. Runs after every render but only acts when a focus was queued —
  // so editing a text field never yanks focus back to the card.
  useEffect(() => {
    if (focusNext.current) {
      document.getElementById(focusNext.current)?.focus();
      focusNext.current = null;
    }
  });

  // Close the open add-object picker on outside click or Escape.
  useEffect(() => {
    if (!pickerKey) return;
    const onDown = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest(".obj-picker-wrap")) setPickerKey(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPickerKey(null);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [pickerKey]);

  /** A detached copy with a fresh element_id — a paste must never duplicate an
   *  id (that would collide in React keys and element-pinned feedback refs). */
  function reid(src: PageObject): PageObject {
    return {
      ...(JSON.parse(JSON.stringify(src)) as PageObject),
      element_id: `edit:${src.type}:${Date.now().toString(36)}:${Math.random()
        .toString(36)
        .slice(2, 6)}`,
    };
  }

  function copyObject(o: PageObject) {
    setClipboard(o);
  }

  /** Insert a detached copy of `src` immediately below `afterId` in its column,
   *  and focus it. Shared by paste and duplicate. */
  function insertCopyAfter(src: PageObject, afterId: string) {
    const loc = locate(pages, afterId);
    if (!loc) return;
    const obj = reid(src);
    const next = clone(pages).map(normColumns);
    next[loc.pi].columns[loc.ci].splice(loc.oi + 1, 0, obj);
    focusNext.current = obj.element_id;
    setSelectedId(obj.element_id);
    emit(next);
  }

  /** Paste the clipboard object immediately below `afterId` in its column. */
  function pasteAfter(afterId: string) {
    if (!clipboard) return;
    insertCopyAfter(clipboard, afterId);
  }

  /** Duplicate a card in place, right below itself.
   *
   *  The copy gets a FRESH element_id, because that id IS the link to a ②
   *  Sandbox object and one sandbox object backs at most one report object. So a
   *  duplicate starts UNLINKED, carrying the original's data snapshot until it
   *  is linked or rebuilt — which the sandbox-coverage badge will report
   *  honestly rather than counting one sandbox object twice. */
  function cloneObject(id: string) {
    const loc = locate(pages, id);
    if (!loc) return;
    insertCopyAfter(pages[loc.pi].columns[loc.ci][loc.oi], id);
  }

  /** Delete `id` and focus a neighbour so the keyboard flow keeps going. */
  function deleteAndReselect(id: string) {
    const loc = locate(pages, id);
    if (!loc) return;
    const col = pages[loc.pi].columns[loc.ci];
    const neighbour = col[loc.oi + 1] ?? col[loc.oi - 1] ?? null;
    focusNext.current = neighbour?.element_id ?? null;
    setSelectedId(neighbour?.element_id ?? null);
    del(id);
  }

  function onCardKeyDown(e: React.KeyboardEvent<HTMLDivElement>, o: PageObject) {
    // Only when the card itself holds focus — never hijack typing in the edit
    // panel's fields (their key events bubble up to this same handler).
    if (e.target !== e.currentTarget) return;
    const meta = e.metaKey || e.ctrlKey;
    const k = e.key.toLowerCase();
    if (meta && k === "c") {
      e.preventDefault();
      copyObject(o);
    } else if (meta && k === "v") {
      e.preventDefault();
      pasteAfter(o.element_id);
    } else if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      deleteAndReselect(o.element_id);
    } else if (e.key === "Escape") {
      setClipboard(null);
      setSelectedId(null);
    }
  }

  function patchData(id: string, key: string, value: unknown) {
    const loc = locate(pages, id);
    if (!loc) return;
    const next = clone(pages);
    const obj = next[loc.pi].columns[loc.ci][loc.oi];
    obj.data = { ...obj.data, [key]: value };
    emit(next);
  }

  /** Move the open/selected state from one element_id to another (used when a
   *  bind/unlink changes an object's id, so its edit panel stays put). */
  function reidState(oldId: string, newId: string) {
    setOpen((prev) => {
      if (!prev.has(oldId)) return prev;
      const n = new Set(prev);
      n.delete(oldId);
      n.add(newId);
      return n;
    });
    setSelectedId((prev) => (prev === oldId ? newId : prev));
  }

  /** Link an object to a ② Sandbox object: it takes that object's type + data +
   *  element_id, so it renders (and reproduces) the sandbox visualisation. The
   *  shared element_id IS the link the sandbox view + coverage read. */
  function bindLinked(id: string, src: PageObject) {
    const loc = locate(pages, id);
    if (!loc) return;
    const next = clone(pages);
    next[loc.pi].columns[loc.ci][loc.oi] = JSON.parse(JSON.stringify(src)) as PageObject;
    reidState(id, src.element_id);
    emit(next);
  }

  /** Unlink an object: give it a fresh unique element_id (keeps its current data),
   *  so it no longer matches a sandbox object. */
  function unlink(id: string) {
    const loc = locate(pages, id);
    if (!loc) return;
    const next = clone(pages);
    const obj = next[loc.pi].columns[loc.ci][loc.oi];
    const newId = `edit:${obj.type}:${Date.now().toString(36)}:${Math.random()
      .toString(36)
      .slice(2, 6)}`;
    obj.element_id = newId;
    reidState(id, newId);
    emit(next);
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
    emit(next);
  }

  /** Move an object to a specific page + column (appends to the target column's
   *  end). The column is clamped to the target page's width, since a two-col page's
   *  column 2 has no equivalent on a one-col page. Powers the page/column pickers
   *  in the edit panel so a card can be relocated across pages without dragging. */
  function moveTo(id: string, pi: number, ci: number) {
    const loc = locate(pages, id);
    if (!loc || pi < 0 || pi >= pages.length) return;
    const targetCols = colCount(normTemplate(pages[pi].template));
    const col = Math.max(0, Math.min(ci, targetCols - 1));
    if (loc.pi === pi && loc.ci === col) return;
    const next = clone(pages);
    const [obj] = next[loc.pi].columns[loc.ci].splice(loc.oi, 1);
    if (!next[pi].columns[col]) next[pi].columns[col] = [];
    next[pi].columns[col].push(obj);
    emit(next);
  }

  /** Drop onto a card: place before it. Drop onto a column: append. */
  function onDropCard(targetId: string) {
    const src = dragId.current;
    dragId.current = null;
    if (!src || src === targetId) return;
    const s = locate(pages, src);
    if (!s) return;
    const next = clone(pages);
    const [obj] = next[s.pi].columns[s.ci].splice(s.oi, 1);
    const t = locate(next, targetId);
    if (!t) {
      emit(next);
      return;
    }
    next[t.pi].columns[t.ci].splice(t.oi, 0, obj);
    emit(next);
  }

  function onDropColumn(pi: number, ci: number) {
    const src = dragId.current;
    dragId.current = null;
    if (!src) return;
    const s = locate(pages, src);
    if (!s) return;
    const next = clone(pages);
    const [obj] = next[s.pi].columns[s.ci].splice(s.oi, 1);
    next[pi].columns[ci].push(obj);
    emit(next);
  }

  /** The card's edit panel: what it's called, where it sits, what data backs it,
   *  and — for a builder-authored object — the Structured Builder itself,
   *  expanded INSIDE its own box rather than pushed below the panel, so the
   *  controls stay visually attached to the button that opened them. */
  const editPanel = (o: PageObject, cols: number) => {
    const isChart = o.type === "trend" || o.type === "breakdown" || o.type === "compare";
    const loc = locate(pages, o.element_id);
    // Tables are builder-authored too (a plain table, or a pivot), so they get
    // the same in-place edit as the charts — otherwise the builder could create
    // an object it then refused to reopen.
    const buildable = (isChart || o.type === "table") && !!onEditInBuilder;
    const building = builderPanel && editingId === o.element_id;
    return (
      <div
        style={{
          marginTop: 6,
          paddingTop: 6,
          borderTop: "1px dashed rgba(128,128,128,0.35)",
          display: "flex",
          flexDirection: "column",
          gap: 6,
        }}
      >
        {/* Panel header — the close control lives here, apart from the card's own
            ✕ (which REMOVES the object), so the two are never confused. */}
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ ...label, opacity: 0.5 }}>edit</span>
          <button
            type="button"
            data-testid="close-edit"
            title="close edit"
            aria-label="Close edit"
            style={{ ...ctrl, marginLeft: "auto", padding: "1px 7px" }}
            onClick={() => {
              if (building) onCloseBuilder?.();
              toggleOpen(o.element_id);
            }}
          >
            ✕
          </button>
        </div>

        {TEXT_FIELDS[o.type].map(([key, lbl, multi]) => (
          <label key={key} style={{ display: "block" }}>
            <span style={label}>{lbl}</span>
            {multi ? (
              <textarea
                value={String(o.data[key] ?? "")}
                onChange={(e) => patchData(o.element_id, key, e.target.value)}
                rows={2}
                style={fieldInput}
              />
            ) : (
              <input
                value={String(o.data[key] ?? "")}
                onChange={(e) => patchData(o.element_id, key, e.target.value)}
                style={fieldInput}
              />
            )}
          </label>
        ))}

        {/* Where the object sits, and how tall it draws. */}
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ ...label, opacity: 0.5 }}>location</span>
          {pages.length > 1 && loc && (
            <label style={{ ...label }}>
              page{" "}
              <KitSelect
                testId="move-page"
                ariaLabel="Move to page"
                value={String(loc.pi)}
                onValueChange={(v) => moveTo(o.element_id, Number(v), loc.ci)}
                options={pages.map((_, i) => ({ value: String(i), label: String(i + 1) }))}
              />
            </label>
          )}
          {cols > 1 && loc && (
            <label style={{ ...label }}>
              column{" "}
              <KitSelect
                testId="move-column"
                ariaLabel="Move to column"
                value={String(loc.ci)}
                onValueChange={(v) => moveTo(o.element_id, loc.pi, Number(v))}
                options={Array.from({ length: cols }, (_, i) => ({
                  value: String(i),
                  label: String(i + 1),
                }))}
              />
            </label>
          )}
          {isChart && (
            <label style={{ ...label }}>
              height{" "}
              <KitSelect
                ariaLabel="Chart height"
                value={String(o.data["height"] ?? "md")}
                onValueChange={(v) => patchData(o.element_id, "height", v)}
                options={HEIGHTS.map((h) => ({ value: h, label: h }))}
              />
            </label>
          )}
        </div>

        {/* Link this object to a ② Sandbox object — pick one to base its data on.
            The shared element_id ties the two views together and drives the sandbox
            coverage. Only unlinked sandbox objects (+ the current one) are offered,
            so each sandbox object backs at most one report object. */}
        <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <span style={label}>linked object</span>
          {(() => {
            const usedIds = new Set(pages.flatMap((p) => p.columns.flat()).map((x) => x.element_id));
            const linked = sandboxObjects.some((s) => s.element_id === o.element_id);
            const options = sandboxObjects.filter(
              (s) => s.element_id === o.element_id || !usedIds.has(s.element_id),
            );
            const objTitle = (x: PageObject) => {
              const dd = x.data as Record<string, unknown>;
              return String(dd["title"] ?? dd["label"] ?? dd["heading"] ?? x.type);
            };
            if (sandboxObjects.length === 0) {
              return (
                <span style={{ ...label, opacity: 0.55, textTransform: "none", letterSpacing: 0 }}>
                  run ② Sandbox to produce objects to link to · id {o.element_id}
                </span>
              );
            }
            return (
              <>
                <KitSelect
                  testId="linked-object-select"
                  ariaLabel="Linked sandbox object"
                  className="max-w-[340px]"
                  value={linked ? o.element_id : ""}
                  onValueChange={(v) => {
                    if (!v) unlink(o.element_id);
                    else {
                      const src = sandboxObjects.find((s) => s.element_id === v);
                      if (src) bindLinked(o.element_id, src);
                    }
                  }}
                  options={[
                    { value: "", label: "— unlinked / custom —" },
                    ...options.map((s) => ({
                      value: s.element_id,
                      label: `${s.type} · ${objTitle(s)} (${s.element_id})`,
                    })),
                  ]}
                />
                <span style={{ ...label, opacity: 0.5, textTransform: "none", letterSpacing: 0 }}>
                  {linked
                    ? "✓ data from this sandbox object"
                    : "not linked — pick one to base its data on"}
                </span>
              </>
            );
          })()}
        </div>

        {/* Edit with the Structured Builder. The builder expands INTO this box
            when opened, so the panel grows in place instead of the controls
            appearing somewhere below the card's other fields. */}
        {buildable && (
          <div
            data-testid="builder-box"
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 8,
              padding: 6,
              borderRadius: 6,
              border: `1px solid rgba(120,160,255,${building ? 0.85 : 0.45})`,
              background: "rgba(120,160,255,0.07)",
            }}
          >
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <button
                type="button"
                data-testid="edit-in-builder"
                aria-expanded={!!building}
                style={{
                  ...ctrl,
                  padding: "3px 10px",
                  background: "rgba(120,160,255,0.28)",
                  borderColor: "rgba(120,160,255,0.6)",
                }}
                onClick={() => (building ? onCloseBuilder?.() : onEditInBuilder?.(o))}
              >
                {building ? "▾ edit with Structured Builder" : "◆ edit with Structured Builder"}
              </button>
              <span style={{ ...label, opacity: 0.55, textTransform: "none", letterSpacing: 0 }}>
                change columns · measures · group · filter deterministically, with a preview
              </span>
            </div>
            {building && <div data-testid="inline-builder">{builderPanel}</div>}
          </div>
        )}
      </div>
    );
  };

  const editableCard = (o: PageObject, cols: number) => (
    <div
      key={o.element_id}
      id={o.element_id}
      className={objectCardClass(o)}
      draggable
      tabIndex={0}
      onDragStart={() => {
        dragId.current = o.element_id;
      }}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.stopPropagation();
        onDropCard(o.element_id);
      }}
      onFocus={() => setSelectedId(o.element_id)}
      onClick={() => setSelectedId(o.element_id)}
      onKeyDown={(e) => onCardKeyDown(e, o)}
      style={{
        position: "relative",
        outline: selectedId === o.element_id ? "2px solid rgba(120,160,255,0.85)" : undefined,
        outlineOffset: 2,
      }}
    >
      <div style={{ position: "absolute", top: 4, right: 4, display: "flex", gap: 2, zIndex: 2 }}>
        <button type="button" title="move up" style={ctrl} onClick={() => moveInCol(o.element_id, -1)}>
          ↑
        </button>
        <button type="button" title="move down" style={ctrl} onClick={() => moveInCol(o.element_id, 1)}>
          ↓
        </button>
        <button
          type="button"
          title="duplicate"
          aria-label="Duplicate object"
          data-testid="clone-object"
          style={ctrl}
          onClick={() => cloneObject(o.element_id)}
        >
          ⧉
        </button>
        <button
          type="button"
          title="edit fields"
          style={{ ...ctrl, background: open.has(o.element_id) ? "rgba(120,160,255,0.35)" : ctrl.background }}
          onClick={() => toggleOpen(o.element_id)}
        >
          ✎
        </button>
        <button type="button" title="remove" style={ctrl} onClick={() => del(o.element_id)}>
          ✕
        </button>
      </div>
      <ObjectBody o={o} />
      {open.has(o.element_id) && editPanel(o, cols)}
    </div>
  );

  if (pages.length === 0) {
    return (
      <div style={{ fontSize: 13 }}>
        <div style={{ opacity: 0.6, marginBottom: 6 }}>
          No pages yet — “Draft with agent”, run the sandbox and “Add output as page”, or add a blank
          page and start composing.
        </div>
        <button type="button" style={{ ...ctrl, padding: "4px 10px" }} onClick={addPage}>
          ＋ Add blank page
        </button>
      </div>
    );
  }

  return (
    <div className="report">
      {clipboard && (
        <div
          style={{
            ...label,
            display: "flex",
            gap: 8,
            alignItems: "center",
            marginBottom: 8,
            padding: "4px 8px",
            borderRadius: 6,
            border: "1px solid rgba(120,160,255,0.45)",
            background: "rgba(120,160,255,0.08)",
            color: "rgb(120,160,255)",
          }}
        >
          📋 copied a {OBJECT_TYPE_LABELS[clipboard.type]} — focus a card and press ⌘/Ctrl+V to
          paste it below
          <button type="button" style={{ ...ctrl, marginLeft: "auto" }} onClick={() => setClipboard(null)}>
            clear
          </button>
        </div>
      )}
      {pages.map((rawPage, pi) => {
        const page = normColumns(rawPage);
        const cols = colCount(page.template);
        return (
          <div
            key={pi}
            data-testid={`page-${pi}`}
            style={{
              marginBottom: 14,
              border: "1px solid rgba(128,128,128,0.25)",
              borderRadius: 8,
              padding: 10,
            }}
          >
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8, flexWrap: "wrap" }}>
              <span style={{ fontSize: 11, opacity: 0.7 }}>page {pi + 1}</span>
              <KitSelect
                testId={`page-template-${pi}`}
                ariaLabel={`Page ${pi + 1} template`}
                value={page.template}
                onValueChange={(v) => setTemplate(pi, v as TemplateId)}
                options={TEMPLATE_IDS.map((t) => ({ value: t, label: t }))}
              />
              {cols > 1 && (
                <span
                  style={{ display: "inline-flex", gap: 4, alignItems: "center" }}
                  title="relative column widths (fr) — bigger number = wider column"
                >
                  <span style={{ ...label, opacity: 0.6 }}>widths</span>
                  {columnWeights(page).map((w, ci) => (
                    <input
                      key={ci}
                      type="number"
                      min={0.2}
                      step={0.1}
                      value={w}
                      onChange={(e) =>
                        setColumnWidth(pi, ci, Math.max(0.2, Number(e.target.value) || 1))
                      }
                      style={{ width: 46, fontSize: 11, padding: "1px 3px" }}
                    />
                  ))}
                  {page.widths && (
                    <button
                      type="button"
                      style={ctrl}
                      title="reset to the template's default widths"
                      onClick={() => resetWidths(pi)}
                    >
                      ↺
                    </button>
                  )}
                </span>
              )}
              <button type="button" style={ctrl} title="remove this page" onClick={() => removePage(pi)}>
                ✕ page
              </button>
              <span style={{ fontSize: 11, opacity: 0.5 }}>
                drag cards or use ↑↓ · ⧉ duplicate · ✎ edit · column picker to move · ✕ remove ·
                click a card then ⌘/Ctrl+C copy · ⌘/Ctrl+V paste below · Del remove
              </span>
            </div>
            <input
              value={page.headline ?? ""}
              placeholder="＋ Page headline — summarise what this page shows (optional)"
              onChange={(e) => setHeadline(pi, e.target.value)}
              style={{ ...fieldInput, fontSize: 13, fontWeight: 600, marginBottom: 8 }}
            />
            <div
              style={{
                display: "grid",
                gridTemplateColumns: columnTracks(page).slice(0, cols).join(" "),
                gap: 10,
              }}
            >
              {Array.from({ length: cols }, (_, ci) => {
                const key = `${pi}:${ci}`;
                return (
                  <div
                    key={ci}
                    data-testid={`col-${pi}-${ci}`}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={() => onDropColumn(pi, ci)}
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 8,
                      minHeight: 60,
                      padding: 6,
                      borderRadius: 6,
                      border: "1px dashed rgba(128,128,128,0.25)",
                    }}
                  >
                    <div style={{ ...label, opacity: 0.5 }}>column {ci + 1}</div>
                    {page.columns[ci].map((o) => editableCard(o, cols))}
                    <div className="obj-picker-wrap" style={{ marginTop: "auto" }}>
                      <button
                        type="button"
                        data-testid={`add-btn-${pi}-${ci}`}
                        className="obj-add-btn"
                        aria-haspopup="menu"
                        aria-expanded={pickerKey === key}
                        onClick={() => setPickerKey((k) => (k === key ? null : key))}
                      >
                        ＋ Add object
                      </button>
                      {pickerKey === key && (
                        <div className="obj-picker" role="menu">
                          <div className="obj-picker-head">
                            Add to <b>column {ci + 1}</b>
                          </div>
                          {OBJECT_TYPES.map((t) => (
                            <button
                              key={t}
                              type="button"
                              role="menuitem"
                              className="obj-row"
                              data-testid={`add-opt-${pi}-${ci}-${t}`}
                              onClick={() => addObject(pi, ci, t)}
                            >
                              <span className="obj-row-glyph">{OBJECT_GLYPHS[t]}</span>
                              <span className="obj-row-text">
                                <b>{OBJECT_TYPE_LABELS[t]}</b>
                                <span>{OBJECT_TYPE_DESCRIPTIONS[t]}</span>
                              </span>
                            </button>
                          ))}
                          {/* A pivot RENDERS as a table, so it isn't its own object
                              type — but a curator looks for it here, next to the
                              things they add. Picking it drops in the table and
                              opens the builder on it, already in pivot mode. */}
                          {onEditInBuilder && (
                            <button
                              type="button"
                              role="menuitem"
                              className="obj-row"
                              data-testid={`add-opt-${pi}-${ci}-pivot`}
                              onClick={() => addObject(pi, ci, "table", "pivot")}
                            >
                              <span className="obj-row-glyph">{PIVOT_GLYPH}</span>
                              <span className="obj-row-text">
                                <b>Pivot table</b>
                                <span>
                                  rows × a pivoted column (e.g. postcode across the top), metrics in
                                  the cells
                                </span>
                              </span>
                            </button>
                          )}
                          {(() => {
                            // Only offer sandbox objects not already placed anywhere in the
                            // page — mirrors the edit-panel's linked-object-select guard, so
                            // linking never creates two PageObjects sharing one element_id.
                            const usedIds = new Set(
                              pages.flatMap((p) => p.columns.flat()).map((x) => x.element_id),
                            );
                            const linkable = sandboxObjects.filter((s) => !usedIds.has(s.element_id));
                            if (linkable.length === 0) return null;
                            return (
                              <div className="obj-picker-linkgroup">
                                <div className="obj-picker-foot">↳ link a ② Sandbox output</div>
                                {linkable.map((s) => (
                                  <button
                                    key={s.element_id}
                                    type="button"
                                    role="menuitem"
                                    className="obj-row obj-row-link"
                                    data-testid={`add-link-${pi}-${ci}-${s.element_id}`}
                                    onClick={() => addLinked(pi, ci, s)}
                                  >
                                    <span className="obj-row-glyph">{OBJECT_GLYPHS[s.type]}</span>
                                    <span className="obj-row-text">
                                      <b>{objectTitle(s) || OBJECT_TYPE_LABELS[s.type]}</b>
                                      <span>linked · carries real data</span>
                                    </span>
                                  </button>
                                ))}
                              </div>
                            );
                          })()}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
      <button type="button" style={{ ...ctrl, padding: "4px 10px" }} onClick={addPage}>
        ＋ Add blank page
      </button>
    </div>
  );
}
