// SearchableSelect — a combobox: a text box that filters a list as you type and
// shows matching options sorted ascending. Two modes: static `options` (domain
// dims, filtered locally) or async `fetchOptions` (typeahead dims like postcode,
// queried as you type). Replaces the bare <select>/<input> in the filter editor
// so users can both SEE and search dimension values.
import { useEffect, useMemo, useRef, useState } from "react";

function ascending(a: string | number, b: string | number): number {
  const na = Number(a);
  const nb = Number(b);
  if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
  return String(a).localeCompare(String(b));
}

export function SearchableSelect({
  value,
  onChange,
  options,
  fetchOptions,
  placeholder = "value…",
  ariaLabel,
}: {
  value: string;
  onChange: (v: string) => void;
  options?: (string | number)[];
  fetchOptions?: (q: string) => Promise<(string | number)[]>;
  placeholder?: string;
  ariaLabel?: string;
}) {
  const [query, setQuery] = useState(value);
  const [open, setOpen] = useState(false);
  const [remote, setRemote] = useState<(string | number)[]>([]);
  const boxRef = useRef<HTMLDivElement | null>(null);

  // Keep the visible text in sync when the value is set from outside (Ask-AI).
  useEffect(() => setQuery(value), [value]);

  // Async fetch (typeahead dims), debounced.
  useEffect(() => {
    if (!fetchOptions || !open) return;
    const id = window.setTimeout(() => {
      void fetchOptions(query).then(setRemote).catch(() => setRemote([]));
    }, 180);
    return () => window.clearTimeout(id);
  }, [query, open, fetchOptions]);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const shown = useMemo(() => {
    const source = fetchOptions ? remote : (options ?? []);
    const q = query.trim().toLowerCase();
    const filtered =
      fetchOptions || !q
        ? [...source]
        : source.filter((o) => String(o).toLowerCase().includes(q));
    return filtered.sort(ascending).slice(0, 50);
  }, [options, remote, query, fetchOptions]);

  function pick(v: string | number) {
    onChange(String(v));
    setQuery(String(v));
    setOpen(false);
  }

  return (
    <div className="ex-combo" ref={boxRef}>
      <input
        type="text"
        className="ex-combo-input"
        value={query}
        placeholder={placeholder}
        aria-label={ariaLabel}
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
          // Commit free text too, so typing a value that isn't listed still filters.
          onChange(e.target.value);
        }}
      />
      {open && shown.length > 0 && (
        <ul className="ex-combo-list" role="listbox">
          {shown.map((o) => (
            <li key={String(o)}>
              <button
                type="button"
                className="ex-combo-opt"
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(o);
                }}
              >
                {String(o)}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
