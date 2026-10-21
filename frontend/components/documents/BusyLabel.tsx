import type { ReactNode } from "react";
import styles from "./Documents.module.css";

/**
 * A button's label that keeps the button's width while it works (MonoEdge design system,
 * 16 Oct 2026): the idle and the busy words share one grid cell and the one not shown is
 * hidden, never removed, so the button is as wide as the longer of the two. The spinner
 * shows only after 300 ms (globals.css .delayed), so a quick reply never flashes one.
 */
export function BusyLabel({ busy, idle, working }: { busy: boolean; idle: ReactNode; working: ReactNode }) {
  return (
    <span className={styles.busyLabel} data-busy={busy}>
      <span>{idle}</span>
      <span>
        {/* "delayed" only while busy: its fade-in sets visibility, which would show it inside the hidden label. */}
        <span className={busy ? "delayed" : undefined} aria-hidden="true">
          <span className="spinner" />
        </span>
        {working}
      </span>
    </span>
  );
}
