// An empty place (Components.html, 16): paper and signal, not cartoons. Two
// ledger sheets, what is missing in a heading, what to do about it, and the
// one action that does it. Ledger is the drawing on its own, for other places
// (the drop zone).

import type { CSSProperties, ReactNode } from "react";
import { cx } from "../cx";
import styles from "./EmptyState.module.css";

export function Ledger({ className, style }: { className?: string; style?: CSSProperties }) {
  return <span className={cx(styles.ledger, className)} style={style} aria-hidden="true" />;
}

export interface EmptyStateProps {
  title: ReactNode;
  children?: ReactNode;
  /** The one action: "Upload to Survey 44, Wakad". */
  action?: ReactNode;
}

export function EmptyState({ title, children, action }: EmptyStateProps) {
  return (
    <div className={styles.empty}>
      <div className={styles.stack}>
        <Ledger className={styles.back} />
        <Ledger className={styles.front} />
      </div>
      <h3 className="k-h3">{title}</h3>
      {children ? <p>{children}</p> : null}
      {action}
    </div>
  );
}
