// What happened, newest first (Components.html, 14): a dot with the action's
// icon, accent for a version, dashed review for a label change, then who did
// what and when.

import type { ReactNode } from "react";
import { cx } from "../cx";
import { Icon } from "../Icon/Icon";
import type { KIconName } from "../Icon/Icon";
import styles from "./Timeline.module.css";

export interface TimelineEntry {
  key: string;
  icon: KIconName;
  tone?: "default" | "accent" | "review";
  /** "<strong>Rahul</strong> linked version 2". */
  text: ReactNode;
  /** "today 10:42". */
  when: ReactNode;
}

export function Timeline({ entries }: { entries: TimelineEntry[] }) {
  return (
    <ol className={styles.timeline}>
      {entries.map((entry) => (
        <li key={entry.key}>
          <span
            className={cx(styles.dot, entry.tone === "accent" && styles.accent, entry.tone === "review" && styles.review)}
          >
            <Icon name={entry.icon} />
          </span>
          <span className={styles.text}>
            <span>{entry.text}</span>
            <span className={styles.when}>{entry.when}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}
