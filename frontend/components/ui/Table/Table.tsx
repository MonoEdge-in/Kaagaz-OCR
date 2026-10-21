// A sortable list, dense and calm (Components.html, 10). The table scrolls inside
// its own box on a narrow screen, so the page never does. A sortable column
// header is a button; the sorted one is in accent with an arrow, and its <th>
// carries aria-sort. A selected row is accent-soft. BulkBar is the dark bar of
// actions for the selected rows.

import type { HTMLAttributes, ReactNode, TableHTMLAttributes, TdHTMLAttributes } from "react";
import { cx } from "../cx";
import { Button } from "../Button/Button";
import { Icon } from "../Icon/Icon";
import styles from "./Table.module.css";

export function Table({ className, children, ...rest }: TableHTMLAttributes<HTMLTableElement>) {
  return (
    <div className={styles.wrap}>
      <table {...rest} className={cx(styles.table, className)}>
        {children}
      </table>
    </div>
  );
}

export type SortDirection = "ascending" | "descending";

export interface SortHeaderProps {
  label: string;
  /** The direction when this column is the sorted one. */
  sort?: SortDirection;
  onSort(): void;
}

export function SortHeader({ label, sort, onSort }: SortHeaderProps) {
  return (
    <th aria-sort={sort}>
      <button type="button" className={cx(styles.sort, sort && styles.sorted)} onClick={onSort}>
        {label}
        {sort ? <Icon name="chev-down" size="sm" className={sort === "ascending" ? styles.up : undefined} /> : null}
      </button>
    </th>
  );
}

export function Row({ selected, className, ...rest }: HTMLAttributes<HTMLTableRowElement> & { selected?: boolean }) {
  return <tr {...rest} className={cx(selected && styles.selected, className) || undefined} />;
}

/** A cell; `num` sets figures right-aligned in mono. */
export function Cell({ num, className, ...rest }: TdHTMLAttributes<HTMLTableCellElement> & { num?: boolean }) {
  return <td {...rest} className={cx(num && styles.num, className) || undefined} />;
}

/** The small paper icon with the file's kind on it: PDF, XLS, JPG. */
export function FileIcon({ ext }: { ext: string }) {
  return <span className={styles.file} data-ext={ext} aria-hidden="true" />;
}

export interface NameCellProps {
  ext: string;
  name: ReactNode;
  /** A line under the name: the type in Marathi, or a ProblemFlag. */
  sub?: ReactNode;
}

export function NameCell({ ext, name, sub }: NameCellProps) {
  return (
    <span className={styles.name}>
      <FileIcon ext={ext} />
      {sub ? (
        <span className={styles.nameText}>
          <span>{name}</span>
          {sub}
        </span>
      ) : (
        <span>{name}</span>
      )}
    </span>
  );
}

/** A quiet second line, in muted text. */
export function SubLine({ children, deva }: { children: ReactNode; deva?: boolean }) {
  return <span className={cx(styles.sub, deva && "k-deva")}>{children}</span>;
}

export interface BulkBarProps {
  /** "1 selected", "3 selected". */
  count: ReactNode;
  /** The action buttons: Move, Tag, Read again, Move to Trash. Small secondary Buttons. */
  children: ReactNode;
  onClear(): void;
}

export function BulkBar({ count, children, onClear }: BulkBarProps) {
  return (
    <div className={styles.bulk} role="region" aria-label="Selection actions">
      <strong>{count}</strong>
      {children}
      <Button size="sm" className={styles.clear} onClick={onClear}>
        Clear selection
      </Button>
    </div>
  );
}
