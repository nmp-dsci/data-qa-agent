// Pack Inspector (s48 §P2) — the synced template pack's admin tab. What the
// agent's slide/chart menu actually is right now: one card per layout,
// straight from Google via GET/PUT /admin/pack(/layouts/{id}). Editing
// happens in two places on purpose — slides and charts stay in Google (no
// editor here can touch CHART/TABLE placeholders), while enable/withhold and
// the `use_when` sentence that steers the agent's choice live here, because
// that text is read by the model on every run and is worth a fast loop.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getPack, Pack, PackLayout, PackLayoutUpdate, updatePackLayout } from "../../lib/api";
import { formatTime } from "../../lib/format";
import { Annunciator } from "../../ui/flightdeck";

function LayoutIssues({ issues }: { issues: string[] }) {
  if (issues.length === 0) return null;
  return (
    <div className="pack-issues">
      {issues.map((issue) => (
        <Annunciator key={issue} state="warn">
          {issue}
        </Annunciator>
      ))}
    </div>
  );
}

function LayoutCard({
  layout,
  onSave,
  saving,
}: {
  layout: PackLayout;
  onSave: (id: string, update: PackLayoutUpdate) => void;
  saving: boolean;
}) {
  const [useWhen, setUseWhen] = useState(layout.use_when);
  const dirty = useWhen !== layout.use_when;

  return (
    <div className="config-card pack-card" data-testid="pack-layout">
      <div className="pack-thumb">
        {layout.thumbnail_url ? (
          <img src={layout.thumbnail_url} alt={`${layout.name} thumbnail`} loading="lazy" />
        ) : (
          <div className="pack-thumb-empty muted">no thumbnail</div>
        )}
      </div>
      <div className="pack-card-body">
        <h4>
          {layout.name} <code className="config-svc">{layout.id}</code>
        </h4>
        <label className="pack-toggle">
          <input
            type="checkbox"
            checked={layout.enabled}
            disabled={saving}
            onChange={(e) => onSave(layout.id, { enabled: e.target.checked })}
          />
          enabled
        </label>
        <textarea
          value={useWhen}
          onChange={(e) => setUseWhen(e.target.value)}
          rows={3}
          aria-label={`${layout.name} use_when`}
        />
        <button
          className="chip"
          disabled={!dirty || saving}
          onClick={() => onSave(layout.id, { use_when: useWhen })}
        >
          Save
        </button>
        <div className="pack-slots">
          {layout.slots.map((slot) => (
            <span key={slot} className="badge">
              {slot}
            </span>
          ))}
        </div>
        <div className="pack-meta muted">
          {layout.table_template && <span>table: {layout.table_template}</span>}
          {layout.chart_template && <span>chart: {layout.chart_template}</span>}
          {layout.grader_shape && <span>grader: {layout.grader_shape}</span>}
        </div>
        <LayoutIssues issues={layout.issues} />
      </div>
    </div>
  );
}

export function PackView() {
  const queryClient = useQueryClient();
  const packQ = useQuery({ queryKey: ["admin", "pack"], queryFn: getPack });
  const mutation = useMutation({
    mutationFn: ({ id, update }: { id: string; update: PackLayoutUpdate }) =>
      updatePackLayout(id, update),
    onSuccess: (data: Pack) => queryClient.setQueryData(["admin", "pack"], data),
  });

  if (packQ.isLoading) return <p className="muted">Loading pack...</p>;
  if (packQ.error) return <p className="error">{(packQ.error as Error).message}</p>;
  const pack = packQ.data;
  if (!pack) return null;

  return (
    <section className="pack-view">
      <div className="pack-header">
        <h3>
          {pack.name} <span className="config-svc">v{pack.version}</span>
          {pack.stale && (
            <Annunciator state="warn" title="The Sheet has been edited since the last sync">
              stale
            </Annunciator>
          )}
        </h3>
        {pack.synced_at && <span className="muted">synced {formatTime(pack.synced_at)}</span>}
        <div className="pack-links">
          {pack.slides_url && (
            <a href={pack.slides_url} target="_blank" rel="noreferrer">
              Open Slides pack
            </a>
          )}
          {pack.sheet_url && (
            <a href={pack.sheet_url} target="_blank" rel="noreferrer">
              Open Sheet pack
            </a>
          )}
          {pack.folder_url && (
            <a href={pack.folder_url} target="_blank" rel="noreferrer">
              Drive folder
            </a>
          )}
        </div>
        <p className="muted pack-hint">
          Edit slides and charts in Google; enable/withhold and the use-when line here. Changes
          move the agent fingerprint.
        </p>
        <LayoutIssues issues={pack.issues} />
      </div>
      <div className="pack-grid">
        {pack.layouts.map((layout) => (
          <LayoutCard
            key={layout.id}
            layout={layout}
            saving={mutation.isPending}
            onSave={(id, update) => mutation.mutate({ id, update })}
          />
        ))}
      </div>
    </section>
  );
}
