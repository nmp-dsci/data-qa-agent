// AskBox — the shared "describe it in plain English" control on the Profile and
// Trends tools. Sends the text to /explore/ask; the LLM (offline stub in dev)
// returns manifest-valid tool state, which the parent applies to its controls.
// Profile waits for the user to hit Run; Trends may autorun.
import { useState } from "react";
import { exploreAsk } from "../../lib/api";

export function AskBox({
  mode,
  dataset,
  placeholder,
  onApply,
}: {
  mode: "profile" | "trends";
  dataset: string;
  placeholder: string;
  onApply: (state: Record<string, unknown>) => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function run() {
    const q = text.trim();
    if (!q || busy) return;
    setBusy(true);
    setErr(null);
    try {
      const res = await exploreAsk(q, mode, dataset);
      onApply(res.state);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ex-askbox">
      <span className="ex-ask-icon" aria-hidden="true">
        ✨
      </span>
      <input
        type="text"
        className="ex-ask-input"
        value={text}
        placeholder={placeholder}
        aria-label="Describe what to show"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && run()}
      />
      <button className="ex-ask-go" onClick={run} disabled={busy || !text.trim()}>
        {busy ? "…" : mode === "trends" ? "Set up & run" : "Set up"}
      </button>
      {err && <span className="ex-ask-err">{err}</span>}
    </div>
  );
}
