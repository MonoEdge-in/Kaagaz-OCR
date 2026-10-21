// Version chips (Components.html, 08): "v1", "Current", and "v1 · newer?" with a
// dashed edge when a newer version may exist. ProblemFlag is the red line under
// a name: "2 pages unreadable".

import type { ReactNode } from "react";
import { cx } from "../cx";
import { Icon } from "../Icon/Icon";
import styles from "./VersionChip.module.css";

export type VersionKind = "plain" | "current" | "newer";

export function VersionChip({ kind = "plain", children }: { kind?: VersionKind; children: ReactNode }) {
  return (
    <span className={cx(styles.ver, kind === "current" && styles.current, kind === "newer" && styles.newer)}>
      {children}
    </span>
  );
}

export function ProblemFlag({ children }: { children: ReactNode }) {
  return (
    <span className={styles.problem}>
      <Icon name="alert" size="sm" />
      {children}
    </span>
  );
}
