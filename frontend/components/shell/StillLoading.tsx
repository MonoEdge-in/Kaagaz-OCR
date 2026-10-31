"use client";

// Issue 468: shown next to a screen's loading state once it has waited about ten seconds, as
// Ask's pending answer does (STILL_LOOKING_AFTER_MS, components/chat/logic.ts): the person may
// keep waiting, or ask again. A polite status line; it never takes focus, and nothing moves.
// Mounted while the screen loads; Try again starts the wait over.

import { useEffect, useState } from "react";
import { STILL_LOOKING_AFTER_MS } from "@/components/chat/logic";
import { Icon } from "@/components/ui/Icon";
import styles from "./Shell.module.css";

export const STILL_LOADING_TEXT = "Still loading — your connection may be slow. Keep waiting or";
export const STILL_LOADING_RETRY = "Try again";

export function StillLoading({ onRetry }: { onRetry: () => void }) {
  const [tries, setTries] = useState(0);
  const [slowFor, setSlowFor] = useState<number | null>(null);
  useEffect(() => {
    const timer = window.setTimeout(() => setSlowFor(tries), STILL_LOOKING_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [tries]);
  return (
    <div className={styles.stillLoading} role="status" aria-live="polite">
      {slowFor === tries ? (
        <>
          <span>{STILL_LOADING_TEXT}</span>
          <button
            type="button"
            className="btn"
            onClick={() => {
              setTries((n) => n + 1);
              onRetry();
            }}
          >
            <Icon name="refresh" />
            {STILL_LOADING_RETRY}
          </button>
        </>
      ) : null}
    </div>
  );
}
