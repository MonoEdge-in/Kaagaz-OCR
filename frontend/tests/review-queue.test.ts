import { describe, expect, it } from "vitest";
import {
  appendItems,
  canReview,
  checkChoice,
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
  },
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

describe("who can review", () => {
  it("is reviewers and admins", () => {
    expect(canReview("member")).toBe(false);
    expect(canReview("reviewer")).toBe(true);
    expect(canReview("admin")).toBe(true);
  });
});
