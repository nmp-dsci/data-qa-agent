// Keep a scroll container pinned to its bottom while new content streams in.
// Pinned = the user is within THRESHOLD px of the bottom; scrolling up unpins
// until they return (or hit "jump to latest"). Streamed content growth is
// tracked two ways: a MutationObserver for added nodes/text, and a
// ResizeObserver on the last child so charts that grow after render keep the
// pin honest. The container mounts and unmounts with the thread (the hero
// state renders no <main>), so listeners attach through a callback ref.
import { useCallback, useRef, useState } from "react";

const THRESHOLD = 80;

export function useStickToBottom() {
  const elRef = useRef<HTMLElement | null>(null);
  const detachRef = useRef<(() => void) | null>(null);
  const pinnedRef = useRef(true);
  const [pinned, setPinned] = useState(true);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "auto") => {
    const el = elRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior });
    pinnedRef.current = true;
    setPinned(true);
  }, []);

  const ref = useCallback((el: HTMLElement | null) => {
    detachRef.current?.();
    detachRef.current = null;
    elRef.current = el;
    if (!el) {
      pinnedRef.current = true;
      setPinned(true);
      return;
    }
    const onScroll = () => {
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < THRESHOLD;
      pinnedRef.current = atBottom;
      setPinned(atBottom);
    };
    const follow = () => {
      if (pinnedRef.current) el.scrollTop = el.scrollHeight;
    };
    const ro = new ResizeObserver(follow);
    const observeLast = () => {
      ro.disconnect();
      if (el.lastElementChild) ro.observe(el.lastElementChild);
    };
    const mo = new MutationObserver(() => {
      observeLast();
      follow();
    });
    el.addEventListener("scroll", onScroll, { passive: true });
    mo.observe(el, { childList: true, subtree: true, characterData: true });
    observeLast();
    detachRef.current = () => {
      el.removeEventListener("scroll", onScroll);
      ro.disconnect();
      mo.disconnect();
    };
  }, []);

  return { ref, pinned, scrollToBottom };
}
