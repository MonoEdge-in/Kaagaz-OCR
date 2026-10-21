"use client";

// A short message in the fixed bottom corner (the global .toast), so news never
// pushes the page under the pointer (UI audit, 16 Oct 2026). Done news leaves by
// itself after a few seconds; a problem stays until it is closed. The live region
// is always there, so a screen reader hears each message once.
// A local stand-in until components/ui has the shared Toast: swap this file's body for it.

import { useEffect } from "react";
import { IconButton } from "@/components/ui/IconButton";
import styles from "./Admin.module.css";

export interface Notice {
  text: string;
  problem?: boolean;
}

const DONE_AFTER_MS = 6000;

export function Toast({ notice, onClose }: { notice: Notice | null; onClose: () => void }) {
  useEffect(() => {
    if (!notice || notice.problem) return;
    const timer = window.setTimeout(onClose, DONE_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [notice, onClose]);

  return (
    <div aria-live="polite">
      {notice && (
        <div className="toast" role={notice.problem ? "alert" : undefined}>
          <p className={notice.problem ? `error-text ${styles.toastText}` : styles.toastText}>
            {notice.text}
          </p>
          <IconButton icon="close" label="Close message" className="btn-quiet" onClick={onClose} />
        </div>
      )}
    </div>
  );
}
