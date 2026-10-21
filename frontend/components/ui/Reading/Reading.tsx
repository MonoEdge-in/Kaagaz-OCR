// One reader's reading of a value, in Review (Components.html, 15): which
// reading it is, the value in mono, and a note. The picked one has a 2px
// accent edge, a check and the word "Chosen", so it reads without colour.
// With `onPick` it is the choice itself, a pressed button with its key (1, 2).

import type { ReactNode } from "react";
import { cx } from "../cx";
import { Icon } from "../Icon/Icon";
import { Kbd } from "../Kbd/Kbd";
import styles from "./Reading.module.css";

export interface ReadingProps {
  /** "First reading". */
  label: string;
  value: ReactNode;
  /** "matches page 1 summary", "The 5 is smudged". */
  note?: ReactNode;
  picked?: boolean;
  /** The key that picks it in Review: "1", "2". */
  shortcut?: string;
  onPick?(): void;
}

export function Reading({ label, value, note, picked, shortcut, onPick }: ReadingProps) {
  const inside = (
    <>
      <span className={styles.head}>
        <span className="k-eyebrow">{label}</span>
        {shortcut ? <Kbd>{shortcut}</Kbd> : null}
      </span>
      <span className={styles.value}>{value}</span>
      {picked ? (
        <span className={cx("k-small k-muted", styles.note)}>
          <Icon name="check" size="sm" />
          {note ? <>Chosen · {note}</> : "Chosen"}
        </span>
      ) : note ? (
        <span className="k-small k-muted">{note}</span>
      ) : null}
    </>
  );
  const className = cx(styles.reading, picked && styles.picked);
  if (onPick) {
    return (
      <button type="button" className={className} aria-pressed={picked ?? false} aria-keyshortcuts={shortcut} onClick={onPick}>
        {inside}
      </button>
    );
  }
  return <div className={className}>{inside}</div>;
}
