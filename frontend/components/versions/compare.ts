// The Compare view's two columns (File management V11), from the server's runs: the left
// column is the older text (what stayed, and what was removed), the right the newer (what
// stayed, and what was added). Each change (removed and added runs next to each other) gets
// one number, the same in both columns, so "Next change" goes to it. Pure functions, unit
// tested in node (tests/file-management.test.ts).

import type { CompareRun } from "@/lib/types";

export interface ColumnRun extends CompareRun {
  change: number | null; // the change it belongs to, from 0; null for text that stayed
}

export interface ComparePage {
  page_no: number;
  left: ColumnRun[];
  right: ColumnRun[];
}

/** Both columns of every page, and how many changes there are. O(runs). */
export function compareColumns(pages: readonly { page_no: number; runs: readonly CompareRun[] }[]): {
  pages: ComparePage[];
  changes: number;
} {
  let changes = 0;
  const out = pages.map((page) => {
    const left: ColumnRun[] = [];
    const right: ColumnRun[] = [];
    let open = false; // inside a change: the runs since the last "same"
    for (const run of page.runs) {
      if (run.kind === "same") {
        open = false;
        left.push({ ...run, change: null });
        right.push({ ...run, change: null });
        continue;
      }
      if (!open) {
        open = true;
        changes += 1;
      }
      (run.kind === "removed" ? left : right).push({ ...run, change: changes - 1 });
    }
    return { page_no: page.page_no, left, right };
  });
  return { pages: out, changes };
}

/** The change after `current` (-1: none yet), going round to the first. */
export function nextChange(current: number, changes: number): number {
  if (changes === 0) return -1;
  return (current + 1) % changes;
}
