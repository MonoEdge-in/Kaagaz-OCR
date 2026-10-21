// The page as paper (Components.html, 15). Viewer is the sunken surface the page
// sits on; Paper the sheet; Mark the four marks drawn on it: cited (solid
// accent), unsure word (dashed), couldn't be read (hatched, its text hidden),
// spot not confirmed (dotted). Each mark has its own line style, and its words
// are said to screen readers, so it reads without colour. MarkLegend names them.

import type { CSSProperties, ReactNode } from "react";
import { cx } from "../cx";
import styles from "./Paper.module.css";

export function Viewer({ children, className, style }: { children: ReactNode; className?: string; style?: CSSProperties }) {
  return (
    <div className={cx(styles.viewer, className)} style={style}>
      {children}
    </div>
  );
}

export function Paper({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <div className={styles.paper} style={style}>
      {children}
    </div>
  );
}

export function PaperRule({ style }: { style?: CSSProperties }) {
  return <div className={styles.rule} style={style} aria-hidden="true" />;
}

export type MarkKind = "cited" | "unsure" | "unreadable" | "unconfirmed";

export const MARK_WORDS: Record<MarkKind, string> = {
  cited: "Cited",
  unsure: "Unsure word",
  unreadable: "Couldn’t be read",
  unconfirmed: "Spot not confirmed",
};

export function Mark({ kind, children }: { kind: MarkKind; children: ReactNode }) {
  return (
    <mark className={cx(styles.mark, styles[kind])}>
      <span className="visually-hidden">{MARK_WORDS[kind]}: </span>
      {children}
    </mark>
  );
}

/** The small numbered tag over a cited box on the page. */
export function MarkTag({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <span className={styles.tag} style={style} aria-hidden="true">
      {children}
    </span>
  );
}

export function MarkLegend({ kinds = ["cited", "unsure", "unreadable", "unconfirmed"] }: { kinds?: MarkKind[] }) {
  return (
    <ul className={styles.legend}>
      {kinds.map((kind) => (
        <li key={kind}>
          <i className={styles[kind]} />
          {MARK_WORDS[kind]}
        </li>
      ))}
    </ul>
  );
}
