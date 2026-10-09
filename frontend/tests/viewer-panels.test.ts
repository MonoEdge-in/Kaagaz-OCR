import { describe, expect, it } from "vitest";
import {
  cardSections,
  checkChip,
  checkReasonText,
  groupLinks,
  invoiceSpot,
  languagesText,
  linkText,
  lowResolutionText,
  noCardText,
  normaliseIdentifier,
  spotNote,
} from "@/components/viewer/panels";
import { ALL_CLEAR, attentionPages } from "@/components/viewer/pageProblems";
import { highlightsOf, pageChange, selectSpot } from "@/components/viewer/selection";
import type { DocumentCard, ExtractedValue, PageSummary, Region } from "@/lib/types";

const box = (page: number, x = 0.1): Region => ({
  kind: "box",
  page,
  box: [x, 0.1, x + 0.2, 0.2],
});

const value = (overrides: Partial<ExtractedValue>): ExtractedValue => ({
  id: "v1",
  document_id: "d1",
  record_key: "document",
  field: "invoice_number",
  value: "INV 17",
  raw: "INV 17",
  second_reading: null,
  page_no: 1,
  region: box(1),
  label: "needs_review",
  label_reason: "model_chosen",
  confidence: null,
  label_page_no: null,
  label_region: null,
  ...overrides,
});

const card: DocumentCard = {
  version: 1,
  run_id: null,
  model: "the AI model",
  prompt: "p",
  doc_type: "land_record",
  languages: ["mr", "en"],
  title: null,
  summary: "A land record.",
  document_date: {
    value: "12/08/2026",
    date: "2026-08-12",
    page: 1,
    region: box(1, 0.5),
  },
  parties: [
    {
      role: "owner",
      field: "party_name",
      name: "शिंदे",
      name_latin: "Shinde",
      page: 1,
      region: box(1),
    },
  ],
  identifiers: [],
  key_facts: [
    {
      field: "area",
      value: "2 acres",
      page: 2,
      region: box(2),
      label: "Area",
      label_page: 1,
      label_region: box(1, 0.6),
    },
  ],
  dropped_quotes: 0,
};

describe("cardSections", () => {
  it("lists parties with the Latin spelling, dates and key facts, and leaves out empty sections", () => {
    const sections = cardSections(card, []);
    expect(sections.map((s) => s.heading)).toEqual(["Parties", "Dates", "Key facts"]);
    expect(sections[0]?.items[0]).toMatchObject({
      name: "Owner",
      text: "शिंदे (Shinde)",
    });
    expect(sections[1]?.items[0]).toMatchObject({
      name: "Document date",
      text: "12/08/2026",
    });
    expect(sections[2]?.items[0]).toMatchObject({
      name: "Area",
      text: "2 acres",
      label: box(1, 0.6),
    });
  });

  it("takes a party's label spot from its card value at the same spot", () => {
    const own = value({
      record_key: "card",
      field: "party_name",
      label_region: box(1, 0.7),
    });
    expect(cardSections(card, [own])[0]?.items[0]?.label).toEqual(box(1, 0.7));
  });
});

describe("languagesText", () => {
  it("names each language, or says it is not known", () => {
    expect(languagesText(["mr", "en", "other"])).toBe("Marathi, English, Other");
    expect(languagesText([])).toBe("Not known");
  });
});

describe("linkText", () => {
  const link = {
    id: "d2",
    display_name: "b.pdf",
    doc_type: null,
    identifier: { kind: "invoice_number", value: "inv17" },
  };

  it("spells the identifier as this document does when one of its values matches", () => {
    expect(linkText(link, [value({})])).toBe("Same invoice number: INV 17");
  });

  it("falls back to the stored value, and says PO in capitals", () => {
    expect(linkText(link, [])).toBe("Same invoice number: inv17");
    const po = { ...link, identifier: { kind: "po_number", value: "po-9" } };
    expect(linkText(po, [])).toBe("Same PO number: po-9");
  });

  it("lists a document once, with every number it shares, in the server's order", () => {
    const po = { ...link, identifier: { kind: "po_number", value: "po-9" } };
    const other = { ...link, id: "d3" };
    expect(groupLinks([link, other, po]).map((group) => group.map((l) => l.identifier.kind))).toEqual([
      ["invoice_number", "po_number"],
      ["invoice_number"],
    ]);
    expect(groupLinks([])).toEqual([]);
  });

  it("normalises as the server does, Devanagari digits included", () => {
    expect(normaliseIdentifier(" ४५/२ A ")).toBe("45/2a");
  });
});

describe("checkChip", () => {
  it("says the checks passed, in the verified look", () => {
    expect(checkChip({ check_status: "passed", check_reasons: [] })).toEqual({
      text: "Checks passed",
      label: "verified",
      reasons: [],
    });
  });

  it("keeps the badge short and lists every failed check in plain words", () => {
    const chip = checkChip({
      check_status: "failed",
      check_reasons: ["lines_sum_mismatch", "bad_gstin"],
    });
    expect(chip).toEqual({
      text: "Needs review",
      label: "needs_review",
      reasons: ["Line items don't add up to the subtotal", "A GSTIN isn't valid"],
    });
  });

  it("never says passed when no check could run, and says why", () => {
    expect(checkChip({ check_status: "not_checked", check_reasons: [] })).toEqual({
      text: "Needs review",
      label: "needs_review",
      reasons: ["The amounts couldn't be checked"],
    });
  });

  it("still says needs review when a failed check gives no reason", () => {
    expect(checkChip({ check_status: "failed", check_reasons: [] })).toEqual({
      text: "Needs review",
      label: "needs_review",
      reasons: [],
    });
  });

  it("has words for a reason it does not know", () => {
    expect(checkReasonText("new_rule")).toBe("new rule");
  });
});

describe("invoiceSpot", () => {
  it("finds a field by its stored name, and a line cell by its record", () => {
    const total = value({
      field: "total_amount",
      region: box(2),
      label_region: box(2, 0.5),
    });
    const cell = value({
      record_key: "line:2",
      field: "amount",
      region: box(3),
    });
    expect(invoiceSpot([total, cell], "document", "total_amount")).toEqual({
      name: "Total amount",
      region: box(2),
      label: box(2, 0.5),
    });
    expect(invoiceSpot([total, cell], "line:2", "amount")?.region).toEqual(box(3));
    expect(invoiceSpot([total], "line:1", "amount")).toBeNull();
  });
});

describe("lowResolutionText", () => {
  it("gives the exact warning for a low-resolution page", () => {
    expect(lowResolutionText({ low_resolution: true, effective_dpi: 96.4 })).toBe(
      "This page was scanned at 96 DPI. Small text may be misread. Upload a sharper scan if values look wrong.",
    );
  });

  it("says nothing for a sharp page or one with no image", () => {
    expect(lowResolutionText({ low_resolution: false, effective_dpi: 300 })).toBeNull();
    expect(lowResolutionText({ low_resolution: true, effective_dpi: null })).toBeNull();
  });
});

describe("spot selection", () => {
  it("opens on the item's page and highlights both spots", () => {
    const chosen = selectSpot(box(2), box(1));
    expect(chosen).toEqual({
      pageNo: 2,
      region: box(2),
      valueId: null,
      label: box(1),
    });
    expect(highlightsOf(chosen)).toEqual([box(2), box(1)]);
  });

  it("keeps the highlights on the label's page, and drops them on any other", () => {
    const chosen = selectSpot(box(2), box(1));
    expect(pageChange(chosen, 1)).toEqual({ ...chosen, pageNo: 1 });
    expect(pageChange(chosen, 3)).toEqual({
      pageNo: 3,
      region: null,
      valueId: null,
    });
    expect(highlightsOf(null)).toEqual([]);
  });
});

describe("spotNote and noCardText", () => {
  it("names the label's page only when it differs", () => {
    expect(spotNote({ name: "Area", region: box(2), label: box(1) })).toBe(
      "Value on page 2, label on page 1",
    );
    expect(spotNote({ name: "Area", region: box(2), label: box(2) })).toBe("Page 2");
    expect(spotNote({ name: "Area", region: box(2), label: null })).toBe("Page 2");
  });

  it("explains a missing card without calling it an error", () => {
    expect(noCardText("ready")).toMatch(/^No document card\. The AI model was off/);
    expect(noCardText("clean")).toBe("The document card comes once the document is read.");
  });
});

describe("the Attention panel (PRD 5 U5)", () => {
  const page = (page_no: number, more: Partial<PageSummary> = {}): PageSummary => ({
    page_no,
    width: 595,
    height: 842,
    unit: "px",
    has_image: true,
    route: "ocr",
    effective_dpi: 300,
    low_resolution: false,
    read_by: null,
    read_quality: "good",
    read_issues: [],
    unsure_words: 0,
    illegible_words: 0,
    unpinned_lines: 0,
    accepted_by: null,
    accepted_at: null,
    read_again_requested_at: null,
    ...more,
  });

  it("says so when every page was read clearly", () => {
    expect(attentionPages([page(1), page(2)])).toEqual([]);
    expect(ALL_CLEAR).toBe("Everything on these pages was read clearly.");
  });

  it("lists every problem by page, in page order, in the issue table's words", () => {
    const blurry = { code: "blurry" as const, says: "This page is blurry.", fix: "Scan it again." };
    const pages = [
      page(1),
      page(2, { read_quality: "unreadable", read_issues: ["blurry"], problems: [blurry] }),
      page(3, { read_quality: "uncertain", unsure_words: 14, unpinned_lines: 1, accepted_at: "2026-10-09T10:00:00Z" }),
    ];
    expect(attentionPages(pages)).toEqual([
      { pageNo: 2, lines: ["This page is blurry."], kept: false },
      {
        pageNo: 3,
        lines: ["14 words unsure", "The exact spot of 1 line couldn't be confirmed."],
        kept: true,
      },
    ]);
  });

  it("says an unreadable page with no problems listed couldn't be read", () => {
    expect(attentionPages([page(4, { read_quality: "unreadable", illegible_words: 3 })])).toEqual([
      { pageNo: 4, lines: ["This page couldn't be read.", "3 words couldn't be read."], kept: false },
    ]);
  });
});
