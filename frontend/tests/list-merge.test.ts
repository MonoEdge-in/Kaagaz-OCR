// The documents list's poll keeps unchanged rows as the same objects
// (components/documents/listMerge.ts), so only changed rows render again.

import { describe, expect, it } from "vitest";
import { mergeList } from "@/components/documents/listMerge";
import type { DocumentList, DocumentSummary, TimeLeft } from "@/lib/types";

const timeLeft = (seconds: number): TimeLeft => ({
  seconds,
  since_move_seconds: 0,
  slow_after_seconds: 120,
  items_left: 2,
  item_seconds: 5,
  after_seconds: 3,
  confidence: "high",
  elapsed_seconds: 10,
  done_fraction: 0.4,
});

const row = (id: string, more: Partial<DocumentSummary> = {}): DocumentSummary => ({
  id,
  display_name: `${id}.pdf`,
  detected_type: "pdf",
  size_bytes: 1000,
  status: "clean",
  failure_code: null,
  status_message: "Reading page 2 of 5",
  page_count: 5,
  created_at: "2026-10-16T10:00:00Z",
  uploaded_by_name: "Asha",
  folder_id: null,
  tags: [{ id: "t1", name: "Bills" }],
  deleted_at: null,
  version_no: 1,
  family_id: id,
  can_delete: true,
  attempts: 1,
  max_attempts: 5,
  newer_version: null,
  progress: { step: "reading", done: 2, total: 5 },
  time_left: timeLeft(30),
  doc_type: null,
  languages: [],
  pages_unreadable: 0,
  pages_uncertain: 0,
  values_to_review: 0,
  ...more,
});

// A fresh answer from the server: equal fields, new objects all the way down.
const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const list = (items: DocumentSummary[], total = items.length): DocumentList => ({ items, total });

describe("merging a poll into the documents list", () => {
  it("gives back the old list itself when nothing changed", () => {
    const before = list([row("a"), row("b")]);
    expect(mergeList(before, copy(before))).toBe(before);
  });

  it("keeps an unchanged row's object and takes the changed row's new one", () => {
    const before = list([row("a"), row("b")]);
    const next = copy(before);
    next.items[1] = row("b", { status_message: "Reading page 3 of 5" });
    const merged = mergeList(before, next);
    expect(merged).not.toBe(before);
    expect(merged.items[0]).toBe(before.items[0]);
    expect(merged.items[1]).toBe(next.items[1]);
  });

  it("passes a new time_left on as a new object, for TimeLeft to see", () => {
    const before = list([row("a")]);
    const next = list([row("a", { time_left: timeLeft(25) })]);
    const merged = mergeList(before, next);
    expect(merged.items[0]).toBe(next.items[0]);
    expect(merged.items[0]?.time_left).toBe(next.items[0]?.time_left);
    expect(merged.items[0]?.time_left).not.toBe(before.items[0]?.time_left);
  });

  it("keeps an unchanged row's time_left object, so its countdown is not restarted", () => {
    const before = list([row("a")]);
    expect(mergeList(before, copy(before)).items[0]?.time_left).toBe(before.items[0]?.time_left);
  });

  it("follows the new order, new rows, rows gone and a new total", () => {
    const a = row("a");
    const b = row("b");
    const before = list([a, b]);
    const reordered = mergeList(before, copy(list([b, a])));
    expect(reordered.items).toEqual([b, a]);
    expect(reordered.items[0]).toBe(b);
    const c = row("c");
    const grown = mergeList(before, list([copy(a), copy(b), c]));
    expect(grown.items[2]).toBe(c);
    expect(grown.items[0]).toBe(a);
    const shrunk = mergeList(before, list([copy(a)]));
    expect(shrunk.items).toHaveLength(1);
    expect(shrunk.items[0]).toBe(a);
    const moreInAll = mergeList(before, list(copy([a, b]), 40));
    expect(moreInAll).not.toBe(before);
    expect(moreInAll.total).toBe(40);
    expect(moreInAll.items[0]).toBe(a);
  });
});
