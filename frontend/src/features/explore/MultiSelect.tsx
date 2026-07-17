// MultiSelect — choose which dimension values are IN the analysis. With a finite
// domain (`allByDefault`), EVERY value starts checked (all included); unticking a
// value filters it out. Checked (included) values pin to the top so you can still
// scan everything below. Each option carries a translucent distribution bar sized
// by how much data it holds, so you see whether toggling a value moves a lot.
// Typeahead mode (postcode) can't enumerate everything, so there it's the plain
// "tick to include" model.
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
}: {
  selected: (string | number)[];
  onChange: (vals: (string | number)[]) => void;
  options?: DomainValue[];
  fetchOptions?: (q: string) => Promise<(string | number)[]>;
  /** Domain mode: an empty selection means ALL values are included (all checked). */
  allByDefault?: boolean;
  ariaLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [remote, setRemote] = useState<(string | number)[]>([]);
  const boxRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!fetchOptions || !open) return;
    const id = window.setTimeout(() => {
      void fetchOptions(query).then(setRemote).catch(() => setRemote([]));
    }, 180);
    return () => window.clearTimeout(id);
  }, [query, open, fetchOptions]);

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

  // The set that is currently IN the analysis. In allByDefault mode an empty
  // selection means "everything".
  const includedSet = useMemo(() => {
    if (allByDefault && selected.length === 0) return new Set(allValues.map(String));
    return new Set(selected.map(String));
  }, [allByDefault, selected, allValues]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (fetchOptions) return remote.map((v) => ({ value: v, count: 0 }));
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
  }, [options, remote, query, fetchOptions, includedSet]);

  function toggle(v: string | number) {
    const s = String(v);
    if (allByDefault) {
      const next = new Set(includedSet);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      const arr = allValues.filter((x) => next.has(String(x)));
      // All included -> emit [] (no filter = everything); otherwise the kept set.
      onChange(arr.length === allValues.length ? [] : arr);
    } else {
      const next = includedSet.has(s)
        ? selected.filter((x) => String(x) !== s)
        : [...selected, v];
      onChange(next);
    }
  }

  const summary = (() => {
    if (allByDefault) {
      if (includedSet.size === 0 || includedSet.size === allValues.length) return "all";
      const inc = allValues.filter((v) => includedSet.has(String(v)));
      return inc.length <= 2 ? inc.join(", ") : `${inc.length} of ${allValues.length}`;
    }
    if (selected.length === 0) return "any";
    return selected.length <= 2 ? selected.join(", ") : `${selected.slice(0, 2).join(", ")} +${selected.length - 2}`;
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
                    <span className="ex-multi-check">{on ? "☑" : "☐"}</span>
                    <span className="ex-multi-val">{String(o.value)}</span>
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
