// The folder tree (Components.html, 09): group headings, then one link per
// folder, indented 16px per level, with a caret when it has folders inside, an
// icon and a count in mono. The current folder is marked aria-current.
// Opening and closing a level is the screen's: it passes `expanded` and
// `onToggle`, and the caret becomes a button with words of its own.

import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";
import { cx } from "../cx";
import { Icon } from "../Icon/Icon";
import type { KIconName } from "../Icon/Icon";
import styles from "./Tree.module.css";

export function Tree({ label, children }: { label: string; children: ReactNode }) {
  return (
    <nav className={styles.tree} aria-label={label}>
      {children}
    </nav>
  );
}

export function TreeGroup({ children }: { children: ReactNode }) {
  return <span className={styles.group}>{children}</span>;
}

export interface TreeItemProps {
  href: string;
  label: string;
  /** 0 at the top. */
  level?: number;
  icon?: KIconName;
  count?: ReactNode;
  active?: boolean;
  /** Left out for a folder with nothing inside it. */
  expanded?: boolean;
  onToggle?(): void;
}

export function TreeItem({ href, label, level = 0, icon, count, active, expanded, onToggle }: TreeItemProps) {
  const caret =
    expanded === undefined ? null : onToggle ? (
      <button
        type="button"
        className={styles.caret}
        aria-expanded={expanded}
        aria-label={`${expanded ? "Close" : "Open"} ${label}`}
        onClick={(event) => {
          event.preventDefault();
          onToggle();
        }}
      >
        <Icon name={expanded ? "chev-down" : "chev-right"} size="sm" />
      </button>
    ) : (
      <span className={styles.caret}>
        <Icon name={expanded ? "chev-down" : "chev-right"} size="sm" />
      </span>
    );
  return (
    <div style={{ "--lvl": level } as CSSProperties} className={cx(styles.item, active && styles.active)}>
      {caret ?? <span className={styles.caret} />}
      <Link href={href} aria-current={active ? "page" : undefined} className={styles.link}>
        {icon ? <Icon name={icon} /> : null}
        <span className={styles.label}>{label}</span>
        {count !== undefined ? <span className={styles.count}>{count}</span> : null}
      </Link>
    </div>
  );
}
