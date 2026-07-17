// ExplorePage — the Explore tab shell: a dataset picker (filtered to grants) and
// the three tools (Profile · Trends · Data Dictionary). Loads the manifest once
// and hands the active dataset to whichever tool is open.
import { useEffect, useMemo, useState } from "react";
import { ExploreDataset, getExploreDatasets, track } from "../../lib/api";
import { DictionaryTool } from "./DictionaryTool";
import { ProfileTool } from "./ProfileTool";
import { TrendsTool } from "./TrendsTool";

type Tool = "profile" | "trends" | "dictionary";

const TOOLS: { id: Tool; label: string }[] = [
  { id: "profile", label: "Profile" },
  { id: "trends", label: "Trends" },
  { id: "dictionary", label: "Data Dictionary" },
];

export function ExplorePage({ isAdmin = false }: { isAdmin?: boolean }) {
  const [datasets, setDatasets] = useState<ExploreDataset[] | null>(null);
  const [slug, setSlug] = useState<string | null>(null);
  const [tool, setTool] = useState<Tool>("profile");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    track("explore_view");
    getExploreDatasets()
      .then((ds) => {
        setDatasets(ds);
        setSlug((prev) => prev ?? ds[0]?.slug ?? null);
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  const dataset = useMemo(
    () => datasets?.find((d) => d.slug === slug) ?? null,
    [datasets, slug],
  );

  if (error) {
    return (
      <main>
        <p className="ex-error">Could not load Explore: {error}</p>
      </main>
    );
  }
  if (!datasets) {
    return (
      <main aria-busy="true">
        <div className="skel" style={{ height: 40, marginBottom: 12 }} />
        <div className="skel" style={{ height: 260 }} />
      </main>
    );
  }
  if (datasets.length === 0) {
    return (
      <main>
        <p className="muted">
          You don't have access to any explorable datasets yet. Ask an admin for a dataset grant.
        </p>
      </main>
    );
  }

  return (
    <main className="ex-page">
      <div className="ex-top">
        <h2 className="ex-title">Explore</h2>
        <label className="ex-ctrl">
          <span className="ex-ctrl-label">Dataset</span>
          <select
            value={slug ?? ""}
            aria-label="Dataset"
            onChange={(e) => {
              setSlug(e.target.value);
              track("explore_dataset_changed", { dataset: e.target.value });
            }}
          >
            {datasets.map((d) => (
              <option key={d.slug} value={d.slug}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <span className="ex-top-note muted">datasets you're granted</span>
      </div>

      <div className="ex-tabs" role="tablist" aria-label="Explore tools">
        {TOOLS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tool === t.id}
            className={`ex-tab${tool === t.id ? " on" : ""}`}
            onClick={() => setTool(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {dataset && (
        <div className="ex-tool-host" key={`${dataset.slug}-${tool}`}>
          {tool === "profile" && <ProfileTool dataset={dataset} isAdmin={isAdmin} />}
          {tool === "trends" && <TrendsTool dataset={dataset} />}
          {tool === "dictionary" && <DictionaryTool dataset={dataset} />}
        </div>
      )}
    </main>
  );
}
