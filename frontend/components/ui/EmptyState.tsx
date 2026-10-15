// What a screen shows when there is nothing to list yet: two ruled paper sheets
// with the screen's icon on them, a heading, one or two lines, and at most one
// action. The sheets (board 12 P6) are decoration: hidden, still, tokens only.

import type { ReactNode } from "react";
import { Icon } from "./Icon";
import type { IconName } from "./icons";
import styles from "./EmptyState.module.css";

export interface EmptyStateProps {
  icon: IconName;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  headingLevel?: 1 | 2 | 3;
  /** Gives the heading an id and makes it focusable from script, so a page can move focus to it. */
  headingId?: string;
}

export function EmptyState({
  icon,
  title,
  children,
  action,
  headingLevel = 2,
  headingId,
}: EmptyStateProps) {
  const Heading = headingLevel === 1 ? "h1" : headingLevel === 3 ? "h3" : "h2";
  return (
    <div className={styles.empty}>
      <span className={styles.art} aria-hidden="true">
        <svg className={styles.sheets} viewBox="0 0 96 72" focusable="false">
          <g className={styles.sheet} transform="rotate(-8 30 40)">
            <rect x="8" y="10" width="44" height="56" rx="3" />
            <path d="M14 24h32M14 32h32M14 40h32M14 48h32M14 56h32" />
          </g>
          <g className={styles.sheet} data-front="">
            <rect x="34" y="4" width="48" height="60" rx="3" />
            <path d="M34 18h48M34 26h48M34 34h48M34 42h48M34 50h48M34 58h48" />
            <path className={styles.margin} d="M44 4v60" />
          </g>
        </svg>
        <span className={styles.icon}>
          <Icon name={icon} size={20} />
        </span>
      </span>
      <Heading id={headingId} tabIndex={headingId ? -1 : undefined} className={styles.title}>
        {title}
      </Heading>
      {children && <div className={styles.text}>{children}</div>}
      {action && <div className={styles.action}>{action}</div>}
    </div>
  );
}
