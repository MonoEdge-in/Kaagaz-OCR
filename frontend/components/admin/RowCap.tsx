"use client";

// Long tables (MonoEdge design system, 16 Oct 2026): at most 100 rows are drawn, then
// "Show 100 more", so a list of thousands never slows the page down. Used by the Users,
// Organisations, operator Activity and Quality per organisation tables.
// ponytail: a row cap, not true windowing; window the rows (visible + one screen) if a
// table ever needs thousands shown at once.

import { useState } from "react";
import { formatNumber } from "@/lib/format";
import styles from "./Admin.module.css";

export const ROW_CAP = 100;

/** How many rows to draw, and a way to draw the next 100. */
export function useRowCap(): { limit: number; showMore(): void } {
  const [limit, setLimit] = useState(ROW_CAP);
  return { limit, showMore: () => setLimit((n) => n + ROW_CAP) };
}

/** "Showing 100 of 240 users." and its button; nothing once every row is shown. */
export function MoreRows({ shown, total, what, onMore }: { shown: number; total: number; what: string; onMore(): void }) {
  if (shown >= total) return null;
  return (
    <p className={styles.moreRows}>
      <span>
        Showing {formatNumber(shown)} of {formatNumber(total)} {what}.
      </span>
      <button type="button" className="btn" onClick={onMore}>
        Show {formatNumber(Math.min(ROW_CAP, total - shown))} more
      </button>
    </p>
  );
}
