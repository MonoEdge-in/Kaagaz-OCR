// One of the six states (lib/status.ts) as a badge (Components.html, 08): each
// has its own icon and edge (solid, dashed, outlined), and always its words, so
// it reads without colour. Reading breathes instead of showing an icon. The
// words can be more exact than the state's name ("Reading 4 of 12", "Ready"),
// and Failed always travels with its reason and fix nearby (a Banner).

import type { ReactNode } from "react";
import { STATUS_WORDS } from "@/lib/status";
import type { UiStatus } from "@/lib/status";
import { cx } from "../cx";
import { Icon } from "../Icon/Icon";
import type { KIconName } from "../Icon/Icon";
import styles from "./StatusBadge.module.css";

const ICONS: Record<Exclude<UiStatus, "reading">, KIconName> = {
  verified: "check",
  review: "question",
  from: "file",
  notfound: "not-found",
  failed: "alert",
};

export function StatusBadge({ state, children }: { state: UiStatus; children?: ReactNode }) {
  return (
    <span className={cx(styles.status, styles[state])}>
      {state === "reading" ? (
        <span className={styles.dots} aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
      ) : (
        <Icon name={ICONS[state]} />
      )}
      {children ?? STATUS_WORDS[state]}
    </span>
  );
}
