// Shared Explore controls — dataset/metric/dimension selects and the filter-chip
// editor the Profile and Trends tools compose. Kept dumb: they read the dataset
// manifest and emit plain selections; the tools own the fetching.
import { KitSelect } from "@/components/kit/KitSelect";
import { formatPoa, isPoaDimension, stripPoa } from "../../lib/format";
import {
  ExploreDataset,
  ExploreDimension,
  ExploreFilters,
  ExploreFilterValue,
  exploreTypeahead,
} from "../../lib/api";
import { MultiSelect } from "./MultiSelect";
import { SearchableSelect } from "./SearchableSelect";

/** A multi-select's three states, read off a stored filter value: null when the
 *  filter isn't set (a fresh chip), [] when the user explicitly chose nothing,
 *  otherwise the values. See MultiSelect's header for why they stay distinct. */
function toSelection(raw: unknown): (string | number)[] | null {
  if (Array.isArray(raw)) return raw as (string | number)[];
  if (raw == null || raw === "") return null;
  return [raw as string | number];
}
function scalarOf(raw: unknown): string {
  if (Array.isArray(raw)) return raw.length ? String(raw[0]) : "";
  return raw == null ? "" : String(raw);
}

export function Select({
  label,
  value,
  options,
  onChange,
  ariaLabel,
}: {
  label?: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
  ariaLabel?: string;
}) {
  return (
    <label className="ex-ctrl">
      {label && <span className="ex-ctrl-label">{label}</span>}
      <KitSelect
        value={value}
        ariaLabel={ariaLabel ?? label}
        onValueChange={onChange}
        options={options}
      />
    </label>
  );
}

export function MetricSelect({
  dataset,
  value,
  onChange,
  label = "Metric",
}: {
  dataset: ExploreDataset;
  value: string;
  onChange: (v: string) => void;
  label?: string;
}) {
  return (
    <Select
      label={label}
      value={value}
      onChange={onChange}
      options={dataset.metrics.map((m) => ({ value: m.name, label: m.label }))}
    />
  );
}

/** Dimensions a chart/profile can split or group by (optionally excluding the
 *  raw time axis). */
export function splittableDimensions(
  dataset: ExploreDataset,
  { includeTime = false }: { includeTime?: boolean } = {},
): ExploreDimension[] {
  return dataset.dimensions.filter((d) => includeTime || d.kind !== "time");
}

/** The value input for one filter. Multi-selectable dims (categorical/geo) get a
 *  MultiSelect with distribution bars; year/FY get a single searchable dropdown;
 *  postcode/suburb query the typeahead endpoint; time is a plain box. */
function FilterValueInput({
  dim,
  datasetSlug,
  rawValue,
  onChange,
}: {
  dim: ExploreDimension;
  datasetSlug: string;
  rawValue: unknown;
  onChange: (v: ExploreFilterValue | undefined) => void;
}) {
  // null clears the filter (chip and all); [] is kept, because "nothing
  // selected" is a real answer the backend compiles to `false`.
  const emitList = (vals: (string | number)[] | null) => onChange(vals ?? undefined);
  // Postcodes read as bare four-digit numbers; label them as Postal Area codes
  // in the picker (display only — the emitted filter stays the bare code).
  const poa = isPoaDimension(dim.name);
  const poaProps = poa ? { format: formatPoa, transformQuery: stripPoa } : {};

  if (dim.multi && dim.domain && dim.domain.length > 0) {
    return (
      <MultiSelect
        selected={toSelection(rawValue)}
        options={dim.domain}
        allByDefault
        onChange={emitList}
        ariaLabel={`${dim.label} values`}
        {...poaProps}
      />
    );
  }
  if (dim.multi && dim.typeahead) {
    return (
      <MultiSelect
        selected={toSelection(rawValue)}
        fetchOptions={(q) => exploreTypeahead(datasetSlug, dim.name, q)}
        onChange={emitList}
        ariaLabel={`${dim.label} values`}
        {...poaProps}
      />
    );
  }
  if (dim.domain && dim.domain.length > 0) {
    // Single-select (year / financial year) — keeps the sargable month filter.
    return (
      <SearchableSelect
        value={scalarOf(rawValue)}
        onChange={(s) => onChange(s ? coerce(dim, s) : undefined)}
        options={dim.domain.map((d) => d.value)}
        ariaLabel={`${dim.label} value`}
      />
    );
  }
  if (dim.typeahead) {
    return (
      <SearchableSelect
        value={scalarOf(rawValue)}
        onChange={(s) => onChange(s || undefined)}
        fetchOptions={(q) => exploreTypeahead(datasetSlug, dim.name, q)}
        placeholder="search…"
        ariaLabel={`${dim.label} value`}
      />
    );
  }
  return (
    <input
      type="text"
      value={scalarOf(rawValue)}
      aria-label={`${dim.label} value`}
      placeholder={dim.kind === "time" ? "e.g. 2022" : "value…"}
      onChange={(e) => onChange(e.target.value || undefined)}
    />
  );
}

/** Coerce a text value to the type the backend expects for a dimension. */
function coerce(dim: ExploreDimension, raw: string): string | number {
  if ((dim.kind === "ordinal" || dim.kind === "time") && /^-?\d+$/.test(raw)) {
    return Number(raw);
  }
  return raw;
}

/** A cohort/filter editor: a row of active dim=value chips plus an add control.
 *  Emits an ExploreFilters map of equality filters. */
export function FilterEditor({
  dataset,
  filters,
  onChange,
  tone,
  dims: dimsProp,
}: {
  dataset: ExploreDataset;
  filters: ExploreFilters;
  onChange: (f: ExploreFilters) => void;
  tone?: "target" | "comparison";
  /** Which dimensions may be filtered. Explore offers every one; the Goldens
   *  builder narrows it to the mart-backed columns its extract can name. */
  dims?: ExploreDimension[];
}) {
  const dims = dimsProp ?? splittableDimensions(dataset, { includeTime: true });
  const active = Object.keys(filters);
  const available = dims.filter((d) => !active.includes(d.name));

  const setValue = (name: string, raw: ExploreFilterValue | undefined) => {
    const next = { ...filters };
    // An empty array is KEPT: "nothing selected" is a real state the chip has to
    // hold while you tick values back in, and the backend already compiles it to
    // `false`. Only an absent/blank value removes the filter.
    if (raw == null || raw === "") delete next[name];
    else next[name] = raw;
    onChange(next);
  };
  const remove = (name: string) => {
    const next = { ...filters };
    delete next[name];
    onChange(next);
  };
  const add = (name: string) => {
    if (name) onChange({ ...filters, [name]: "" });
  };

  return (
    <div className={`ex-filters${tone ? ` tone-${tone}` : ""}`}>
      {active.map((name) => {
        const dim = dataset.dimensions.find((d) => d.name === name);
        if (!dim) return null;
        return (
          <span className="ex-chip" key={name}>
            <b>{dim.label}</b>
            <FilterValueInput
              dim={dim}
              datasetSlug={dataset.slug}
              rawValue={filters[name]}
              onChange={(val) => setValue(name, val)}
            />
            <button className="ex-chip-x" aria-label={`Remove ${dim.label} filter`} onClick={() => remove(name)}>
              ×
            </button>
          </span>
        );
      })}
      {available.length > 0 && (
        <KitSelect
          className="ex-add-filter"
          value=""
          ariaLabel="Add filter"
          placeholder="+ filter"
          onValueChange={add}
          options={available.map((d) => ({ value: d.name, label: d.label }))}
        />
      )}
    </div>
  );
}
