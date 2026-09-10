// ArtifactView — the answer's presentation surface (s46): an embedded Slides
// deck + link-outs to the deck and its data sheet when the agent built one,
// else the plain SQL/rows view. Replaces the old report-engine (charts/pages)
// rendering entirely — no charts render in the browser any more.
import { useState } from "react";
import { AskResult } from "../../lib/api";

export function ArtifactView({
  result,
  onOpenSql,
}: {
  result: AskResult;
  onOpenSql: (sql: string) => void;
}) {
  if (result.artifact) return <SlidesArtifact artifact={result.artifact} />;
  return <SqlRowsFallback result={result} onOpenSql={onOpenSql} />;
}

function SlidesArtifact({ artifact }: { artifact: NonNullable<AskResult["artifact"]> }) {
  return (
    <div className="artifact">
      <div className="artifact-frame">
        <iframe
          src={artifact.embed_url}
          title="Presentation slides for this answer"
          loading="lazy"
          allowFullScreen
        />
      </div>
      <div className="artifact-actions">
        <a className="ex-run" href={artifact.deck_url} target="_blank" rel="noopener noreferrer">
          Open in Slides
        </a>
        <a
          className="ex-secondary"
          href={artifact.sheet_url}
          target="_blank"
          rel="noopener noreferrer"
        >
          Open the data
        </a>
      </div>
    </div>
  );
}

function SqlRowsFallback({
  result,
  onOpenSql,
}: {
  result: AskResult;
  onOpenSql: (sql: string) => void;
}) {
  const [showSql, setShowSql] = useState(false);
  return (
    <>
      {result.sql && (
        <div className="meta">
          <button className="link" onClick={() => setShowSql((s) => !s)}>
            {showSql ? "hide SQL" : "show SQL"}
          </button>
          <button className="link" onClick={() => onOpenSql(result.sql ?? "")}>
            open in SQL editor
          </button>
        </div>
      )}
      {showSql && result.sql && <pre className="sql">{result.sql}</pre>}
      {result.rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {result.columns.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.slice(0, 25).map((row, ri) => (
                <tr key={ri}>
                  {row.map((cell, ci) => (
                    <td key={ci}>{String(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
