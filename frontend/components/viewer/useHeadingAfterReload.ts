"use client";

// PRD 7 (PR 13): a lock or unlock changes the screen under its dialog (the locked screen, or the
// document again), so the button that opened it is gone. Once the file is loaded again, the
// screen's heading takes the focus instead (DocumentView.tsx, LockedDocument.tsx).

import { useEffect, useRef } from "react";

/** `headingRef` for the screen's heading (tabIndex -1); `focusAfterReload()` before the reload:
 * the next `loaded` (a new detail) focuses it once. */
export function useHeadingAfterReload(loaded: unknown) {
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const pending = useRef(false);
  useEffect(() => {
    if (!pending.current || !loaded) return;
    pending.current = false;
    headingRef.current?.focus();
  }, [loaded]);
  return {
    headingRef,
    focusAfterReload: () => {
      pending.current = true;
    },
  };
}
