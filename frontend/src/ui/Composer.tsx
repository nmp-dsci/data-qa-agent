// The ask composer: auto-growing textarea (Enter sends, Shift+Enter breaks),
// icon send button, stop button while the agent streams. One component serves
// both the hero (empty state) and the docked thread footer.
import { FormEvent, KeyboardEvent, useEffect, useRef } from "react";
import { IconSend, IconStop, PlaneGlyph } from "./icons";

const MAX_HEIGHT = 152; // ~6 lines, then the textarea scrolls internally

export function Composer({
  value,
  onChange,
  onSend,
  onStop,
  busy,
  placeholder,
  autoFocus,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: (text: string) => void;
  onStop?: () => void;
  busy: boolean;
  placeholder: string;
  autoFocus?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
    el.style.overflowY = el.scrollHeight > MAX_HEIGHT ? "auto" : "hidden";
  }, [value]);

  function submit(e?: FormEvent) {
    e?.preventDefault();
    if (busy || !value.trim()) return;
    onSend(value);
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <form className="composer2" onSubmit={submit}>
      <textarea
        ref={ref}
        rows={1}
        value={value}
        placeholder={placeholder}
        autoFocus={autoFocus}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKey}
      />
      <div className="composer2-row">
        <span className="composer2-hint">
          {/* The Sortie idles beside the composer and climbs while the agent
              works — the same plane the login flew, now doing the user's job. */}
          <PlaneGlyph size={13} className={busy ? "composer2-glyph climb" : "composer2-glyph"} />
          ⏎ send · ⇧⏎ newline · ⌘K commands
        </span>
        {busy && onStop ? (
          <button
            type="button"
            className="composer2-stop"
            aria-label="Stop generating"
            title="Stop generating"
            onClick={onStop}
          >
            <IconStop />
          </button>
        ) : (
          <button
            type="submit"
            className="composer2-send"
            aria-label="Ask"
            title="Ask"
            disabled={busy || !value.trim()}
          >
            <IconSend />
          </button>
        )}
      </div>
    </form>
  );
}
