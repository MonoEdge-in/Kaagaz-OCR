import { describe, expect, it } from "vitest";
import {
  appendItems,
  canReview,
  checkChoice,
  checkReasonsText,
  focusAfterResolve,
  groupByDocument,
  leftToReview,
  parseReason,
  queueParams,
  REASON_FILTERS,
  resolvedMessage,
  withoutItem,
} from "@/components/review/review";
import type { ReviewItem } from "@/lib/types";

const item = (id: string, overrides: Partial<ReviewItem["value"]> = {}): ReviewItem => ({
  value: {
    id,
    document_id: "d1",
    record_key: "document",
    field: "total_amount",
    value: "1,234",
    raw: "1,234",
    second_reading: "1,284",
    page_no: 1,
    region: { kind: "box", page: 1, box: [0.1, 0.1, 0.2, 0.2] },
    label: "needs_review",
    label_reason: "readers_disagree",
    confidence: null,
    label_page_no: null,
    label_region: null,
    ...overrides,
  },
  document_id: "d1",
  document_name: "Bill.pdf",
  detected_type: "pdf",
  page: {
    page_no: 1,
    width: 595,
    height: 842,
    unit: "pt",
    has_image: true,
    route: "ocr",
    effective_dpi: 300,
    low_resolution: false,
    read_by: "gemini-3.1-pro-preview",
    read_quality: "good",
    read_issues: [],
    unsure_words: 0,
    illegible_words: 0,
    unpinned_lines: 0,
    accepted_by: null,
    accepted_at: null,
    read_again_requested_at: null,
  },
  check_reasons: [],
});

describe("checkChoice", () => {
  it("always allows the first reading", () => {
    expect(checkChoice("first", "", null)).toEqual({ ok: true, choice: "first" });
  });

  it("allows the second reading only when there is one", () => {
    expect(checkChoice("second", "", "1,284")).toEqual({ ok: true, choice: "second" });
    expect(checkChoice("second", "", null).ok).toBe(false);
  });

  it("needs a typed value of 1 to 500 characters, trimmed", () => {
    expect(checkChoice("typed", "   ", null).ok).toBe(false);
    expect(checkChoice("typed", "x".repeat(501), null).ok).toBe(false);
    expect(checkChoice("typed", "  1,254 ", null)).toEqual({
      ok: true,
      choice: "typed",
      value: "1,254",
    });
  });
});

describe("the queue after a resolve", () => {
  it("drops the item and lowers the total", () => {
    const next = withoutItem({ items: [item("a"), item("b")], total: 7 }, "a");
    expect(next.items.map((i) => i.value.id)).toEqual(["b"]);
    expect(next.total).toBe(6);
  });

  it("changes nothing for an unknown id", () => {
    const next = withoutItem({ items: [item("a")], total: 1 }, "zzz");
    expect(next).toEqual({ items: [item("a")], total: 1 });
  });

  it("adds a further page without repeats", () => {
    const next = appendItems(
      { items: [item("a")], total: 3 },
      { items: [item("a"), item("b")], total: 2 },
    );
    expect(next.items.map((i) => i.value.id)).toEqual(["a", "b"]);
    expect(next.total).toBe(2);
  });

  it("announces the new value in words", () => {
    expect(resolvedMessage(item("a", { value: "1,284" }))).toBe(
      'Saved. Total amount in Bill.pdf is now "1,284".',
    );
  });
});

describe("focus after a resolve", () => {
  const list = (ids: string[], total = ids.length) => ({ items: ids.map((id) => item(id)), total });

  it("moves to the next card", () => {
    expect(focusAfterResolve(list(["a", "b", "c"]), "b")).toEqual({ kind: "card", valueId: "c" });
  });

  it("moves to the previous card when the last one is resolved", () => {
    expect(focusAfterResolve(list(["a", "b", "c"]), "c")).toEqual({ kind: "card", valueId: "b" });
  });

  it("loads the next page when no card is left but the server has more", () => {
    expect(focusAfterResolve(list(["a"], 21), "a")).toEqual({ kind: "next-page" });
  });

  it("moves to the empty heading when nothing is left anywhere", () => {
    expect(focusAfterResolve(list(["a"]), "a")).toEqual({ kind: "empty" });
  });

  it("says how many values are left, in one message with the save", () => {
    expect(leftToReview(44)).toBe("44 values left to review.");
    expect(leftToReview(1)).toBe("1 value left to review.");
    expect(leftToReview(0)).toBe("Nothing left to review.");
  });
});

describe("who can review", () => {
  it("is reviewers and admins", () => {
    expect(canReview("member")).toBe(false);
    expect(canReview("reviewer")).toBe(true);
    expect(canReview("admin")).toBe(true);
  });
});

describe("the reason filter", () => {
  it("offers the four reasons the server accepts", () => {
    expect(REASON_FILTERS.map((choice) => choice.value)).toEqual([
      "check_failed",
      "readers_disagree",
      "low_confidence",
      "single_reader",
      "unsure_text",
      "box_unanchored",
      "local_reader",
      "ocr_script",
    ]);
  });

  it("names PRD 5's reasons in the words of viewer/labels.ts (U6)", () => {
    const text = Object.fromEntries(REASON_FILTERS.map((choice) => [choice.value, choice.text]));
    expect(text.unsure_text).toBe("Hard to read");
    expect(text.box_unanchored).toBe("Spot not confirmed");
    expect(text.local_reader).toBe("Read by the backup reader");
    expect(text.ocr_script).toBe("Read from Hindi or Marathi script");
    expect(parseReason("unsure_text")).toBe("unsure_text");
    expect(queueParams(0, 20, undefined, "box_unanchored")).toEqual({
      limit: 20,
      document_id: undefined,
      reason: ["box_unanchored"],
    });
  });

  it("groups the cards shown by document, in the order each first appears", () => {
    const other = (id: string): ReviewItem => ({ ...item(id), document_id: "d2", document_name: "Register.jpg" });
    const groups = groupByDocument([item("a"), other("b"), item("c")]);
    expect(groups.map((group) => [group.id, group.name, group.items.map((i) => i.value.id)])).toEqual([
      ["d1", "Bill.pdf", ["a", "c"]],
      ["d2", "Register.jpg", ["b"]],
    ]);
    expect(groupByDocument([])).toEqual([]);
  });

  it("reads the select's value, anything else meaning every reason", () => {
    expect(parseReason("low_confidence")).toBe("low_confidence");
    expect(parseReason("")).toBeNull();
    expect(parseReason("corrected")).toBeNull();
  });

  it("asks for one page, filtered only when a reason is chosen", () => {
    expect(queueParams(0, 20, undefined, null)).toEqual({ limit: 20, document_id: undefined });
    expect(queueParams(20, 20, "d1", "check_failed")).toEqual({
      limit: 20,
      offset: 20,
      document_id: "d1",
      reason: ["check_failed"],
    });
  });
});

describe("checkReasonsText", () => {
  it("says why the invoice checks failed, in plain words", () => {
    expect(checkReasonsText({ check_reasons: ["total_mismatch", "missing_total"] })).toBe(
      "The invoice checks failed: the subtotal and tax don't add up to the total; no total was found.",
    );
    expect(checkReasonsText(item("v1"))).toBeNull();
  });
});
