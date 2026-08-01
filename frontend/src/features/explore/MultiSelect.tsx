// MultiSelect — choose which dimension values are IN the analysis. With a finite
// domain (`allByDefault`), EVERY value starts checked (all included); unticking a
// value filters it out. Checked (included) values pin to the top so you can still
// scan everything below. Each option carries a translucent distribution bar sized
// by how much data it holds, so you see whether toggling a value moves a lot.
// Typeahead mode (postcode) can't enumerate everything, so there it's the plain
// "tick to include" model — but the values already picked still pin to the top,
// so searching for the next one never hides the ones you have.
//
// "All / None" are the bulk shortcuts for picking a few values out of many.
// The selection has three states, and they are distinct on the wire because
// conflating them is what used to make unticking the last value silently flip
// back to "everything":
//
//   null  no filter on this dimension at all (the chip carries no predicate)
//   []    nothing selected — the backend compiles this to `false`, matching no
//         rows, which is what the unticked list actually says
//   [..]  the chosen values (an IN filter)
//
// "All" emits null (the filter is gone, so the chip goes with it); "None" emits
// [], leaving the chip in place to tick values into.
import { Square, SquareCheck } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { DomainValue } from "../../lib/api";

function ascending(a: string | number, b: string | number): number {
  const na = Number(a);
  const nb = Number(b);
  if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
  return String(a).localeCompare(String(b));
}

export function MultiSelect({
  selected,
  onChange,
  options,
  fetchOptions,
  allByDefault = false,
  ariaLabel,
  format = String,
  transformQuery,
}: {
  /** null = no filter set; [] = nothing selected; [..] = the chosen values. */
  selected: (string | number)[] | null;
  onChange: (vals: (string | number)[] | null) => void;
  options?: DomainValue[];
  fetchOptions?: (q: string) => Promise<(string | number)[]>;
  /** Domain mode: an unset filter means ALL values are included (all checked). */
  allByDefault?: boolean;
  ariaLabel?: string;
  /** Display-only relabelling of a value (e.g. 2077 -> POA:2077). */
  format?: (v: string | number) => string;
  /** Normalise what the user typed before searching (e.g. drop a "POA:" prefix). */
  transformQuery?: (q: string) => string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [remote, setRemote] = useState<(string | number)[]>([]);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const picked = selected ?? [];

  const search = transformQuery ? transformQuery(query) : query;

  useEffect(() => {
    if (!fetchOptions || !open) return;
    const id = window.setTimeout(() => {
      void fetchOptions(search).then(setRemote).catch(() => setRemote([]));
    }, 180);
    return () => window.clearTimeout(id);
  }, [search, open, fetchOptions]);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const allValues = useMemo(() => (options ?? []).map((o) => o.value), [options]);
  const maxCount = useMemo(
    () => Math.max(1, ...(options ?? []).map((o) => o.count || 0)),
    [options],
  );

  // The set that is currently IN the analysis. In allByDefault mode an UNSET
  // filter means "everything"; an explicitly empty one means nothing.
  const includedSet = useMemo(() => {
    if (allByDefault && selected === null) return new Set(allValues.map(String));
    return new Set((selected ?? []).map(String));
  }, [allByDefault, selected, allValues]);

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (fetchOptions) {
      // Typeahead can't enumerate the domain, so a search would otherwise hide
      // the values already picked. Pin them above the results instead.
      const pinned = picked.map((v) => ({ value: v, count: 0 }));
      const seen = new Set(picked.map(String));
      return [
        ...pinned,
        ...remote.filter((v) => !seen.has(String(v))).map((v) => ({ value: v, count: 0 })),
      ];
    }
    const filtered = q
      ? (options ?? []).filter((o) => String(o.value).toLowerCase().includes(q))
      : [...(options ?? [])];
    // Pin included (checked) values to the top, each group sorted ascending.
    return filtered
      .sort((a, b) => {
        const ai = includedSet.has(String(a.value)) ? 0 : 1;
        const bi = includedSet.has(String(b.value)) ? 0 : 1;
        return ai - bi || ascending(a.value, b.value);
      })
      .slice(0, 200);
  }, [options, remote, search, fetchOptions, includedSet, picked]);

  function toggle(v: string | number) {
    const s = String(v);
    if (allByDefault) {
      const next = new Set(includedSet);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      const arr = allValues.filter((x) => next.has(String(x)));
      // Everything ticked is the same as no filter; anything less is the kept
      // set — including the empty one, which stays empty instead of flipping
      // back to "all" the way it used to.
      onChange(arr.length === allValues.length ? null : arr);
    } else {
      onChange(includedSet.has(s) ? picked.filter((x) => String(x) !== s) : [...picked, v]);
    }
  }

  /** Tick everything — which is the absence of a filter, so the chip clears. */
  function selectAll() {
    onChange(null);
  }
  /** Untick everything, ready to pick a few back. */
  function selectNone() {
    onChange([]);
  }

  const summary = (() => {
    if (allByDefault) {
      if (selected === null || includedSet.size === allValues.length) return "all";
      if (includedSet.size === 0) return "none";
      const inc = allValues.filter((v) => includedSet.has(String(v)));
      return inc.length <= 2 ? inc.map(format).join(", ") : `${inc.length} of ${allValues.length}`;
    }
    if (selected === null) return "any";
    if (picked.length === 0) return "none";
    return picked.length <= 2
      ? picked.map(format).join(", ")
      : `${picked.slice(0, 2).map(format).join(", ")} +${picked.length - 2}`;
  })();

  return (
    <div className="ex-multi" ref={boxRef}>
      <button
        type="button"
        className="ex-multi-trigger"
        aria-label={ariaLabel}
        onClick={() => setOpen((o) => !o)}
      >
        {summary} <span className="ex-multi-caret">▾</span>
      </button>
      {open && (
        <div className="ex-multi-pop" role="listbox" aria-multiselectable="true">
          <input
            type="text"
            className="ex-multi-search"
            value={query}
            placeholder={fetchOptions ? "search…" : "filter…"}
            autoFocus
            onChange={(e) => setQuery(e.target.value)}
          />
          <div className="ex-multi-bulk">
            {/* Typeahead has no enumerable domain, so "all" there is just the
                absence of a filter — one button, named for what it does. */}
            <button type="button" onClick={selectAll}>
              {fetchOptions ? "clear" : "all"}
            </button>
            {!fetchOptions && (
              <button type="button" onClick={selectNone}>
                none
              </button>
            )}
            <span className="ex-multi-bulk-note">{summary}</span>
          </div>
          <ul className="ex-multi-list">
            {shown.map((o) => {
              const on = includedSet.has(String(o.value));
              const pct = options ? Math.round((o.count / maxCount) * 100) : 0;
              return (
                <li key={String(o.value)}>
                  <button
                    type="button"
                    className={`ex-multi-opt${on ? " on" : ""}`}
                    role="option"
                    aria-selected={on}
                    onClick={() => toggle(o.value)}
                  >
                    {options && <span className="ex-multi-bar" style={{ width: `${pct}%` }} />}
                    <span className="ex-multi-check">{on ? <SquareCheck size={13} /> : <Square size={13} />}</span>
                    <span className="ex-multi-val">{format(o.value)}</span>
                    {options && <span className="ex-multi-count">{o.count.toLocaleString()}</span>}
                  </button>
                </li>
              );
            })}
            {shown.length === 0 && <li className="ex-multi-empty">no matches</li>}
          </ul>
        </div>
      )}
    </div>
  );
}
