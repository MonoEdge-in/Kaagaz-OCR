// Filter chips and tags (Components.html, 08). A filter chip is a toggle
// button: off, on with its value ("Department: Accounts") and an x that says
// pressing it removes the filter, a menu chip with a chevron, or a dashed one
// that adds a filter. A tag is a quiet word on a document.

import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cx } from "../cx";
import { Icon } from "../Icon/Icon";
import styles from "./Chip.module.css";

export interface ChipProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "value"> {
  /** On: the filter is applied. Leave out for a chip that only opens a menu or adds. */
  pressed?: boolean;
  /** The chosen value, after the words: "Department: <b>Accounts</b>". */
  value?: ReactNode;
  /** What the chip's end shows: a menu chevron, or the x of an applied filter. */
  end?: "menu" | "remove";
  /** The dashed chip that adds a filter. */
  add?: boolean;
}

export function Chip({ pressed, value, end, add, className, children, type = "button", ...rest }: ChipProps) {
  return (
    <button {...rest} type={type} className={cx(styles.chip, add && styles.add, className)} aria-pressed={pressed}>
      {add ? <Icon name="plus" size="sm" /> : null}
      {children}
      {value !== undefined ? <span className={styles.value}>{value}</span> : null}
      {end === "menu" ? <Icon name="chev-down" size="sm" /> : null}
      {end === "remove" ? (
        <>
          <Icon name="x" size="sm" />
          <span className="visually-hidden">, remove filter</span>
        </>
      ) : null}
    </button>
  );
}

/** A row of chips that wraps. */
export function ChipRow({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx(styles.row, className)}>{children}</div>;
}

/** A tag on a document: "GST", "FY 2026–27". `deva` sets Devanagari text in its own face. */
export function Tag({ children, deva }: { children: ReactNode; deva?: boolean }) {
  return <span className={cx(styles.tag, deva && styles.deva)}>{children}</span>;
}
