// Keep a scroll container pinned to its bottom while new content streams in.
// Pinned = the user is within THRESHOLD px of the bottom; scrolling up unpins
// until they return (or hit "jump to latest"). Streamed content growth is
// tracked two ways: a MutationObserver for added nodes/text, and a
// ResizeObserver on the last child so charts that grow after render keep the
// pin honest.
import { RefObject, useCallback, useEffect, useRef, useState } from "react";

const THRESHOLD = 80;

export function useStickToBottom(ref: RefObject<HTMLElement | null>) {
  const pinnedRef = useRef(true);
  const [pinned, setPinned] = useState(true);

  const scrollToBottom = useCallback(
    (behavior: ScrollBehavior = "auto") => {
      const el = ref.current;
      if (!el) return;
      el.scrollTo({ top: el.scrollHeight, behavior });
      pinnedRef.current = true;
      setPinned(true);
    },
    [ref],
  );

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
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
    return () => {
      el.removeEventListener("scroll", onScroll);
      ro.disconnect();
      mo.disconnect();
    };
  }, [ref]);

  return { pinned, scrollToBottom };
}
