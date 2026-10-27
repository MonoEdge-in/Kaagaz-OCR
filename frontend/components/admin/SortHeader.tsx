// A column header that sorts (#386 members, #387 organisations): a button, with aria-sort on
// the header while it is the sort and an arrow beside its label (hidden from screen readers,
// which hear aria-sort). Pure, so tests render it in node.

import type { SortState } from "./logic";
import styles from "./Admin.module.css";

export function SortHeader<K extends string>({
  column,
  label,
  sort,
  onSort,
  num = false,
}: {
  column: K;
  label: string;
  sort: SortState<K>;
  onSort(column: K): void;
  num?: boolean; // a figure column, lined up at the end
}) {
  const active = sort.column === column;
  return (
    <th scope="col" role="columnheader" className={num ? "num" : undefined} aria-sort={active ? sort.dir : undefined}>
      <button type="button" className={styles.sortButton} onClick={() => onSort(column)}>
        {label}
        {active && (
          <span className={styles.sortMark} aria-hidden="true">
            {sort.dir === "ascending" ? "↑" : "↓"}
          </span>
        )}
      </button>
    </th>
  );
}
