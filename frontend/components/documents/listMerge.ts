// A new answer to the documents list's poll, merged into the one shown (DocumentsView).
// A row whose fields are all the same keeps its old object, so its memoised row
// (DocumentsTable) does not render again; a row with any change, its time_left among
// them, is the new object, so TimeLeft (keyed on time_left's identity) sees the new
// answer. When nothing changed at all, the old list comes back as is.

import type { DocumentList, DocumentSummary } from "@/lib/types";

// The same serializer sends every row, so its keys come in the same order; a
// different order only counts as a change, which renders as before.
const same = (a: DocumentSummary, b: DocumentSummary) => JSON.stringify(a) === JSON.stringify(b);

export function mergeList(previous: DocumentList, next: DocumentList): DocumentList {
  const before = new Map(previous.items.map((item) => [item.id, item]));
  const items = next.items.map((item) => {
    const old = before.get(item.id);
    return old && same(old, item) ? old : item;
  });
  const unchanged =
    next.total === previous.total &&
    items.length === previous.items.length &&
    items.every((item, i) => item === previous.items[i]);
  return unchanged ? previous : { ...next, items };
}
