import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AttentionPanel } from "@/app/(app)/documents/[id]/DocumentPanels";
import { PageViewer } from "@/components/viewer/PageViewer";
import {
  cardSections,
  checkReasonText,
  groupLinks,
  languagesText,
  linkText,
  lowResolutionText,
  noCardText,
  normaliseIdentifier,
  spotNote,
  untilRead,
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
    // Changed on purpose (Tushar, 24 Oct 2026): the Latin spelling is its own smaller line.
    expect(sections[0]?.items[0]).toMatchObject({
      name: "Owner",
      text: "शिंदे",
      sub: "Shinde",
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

// Changed on purpose (Vrushit UAT, 25 Oct 2026): the Invoice section is gone from the document
// page, and with it checkChip, invoiceSpot and the line fields; the review queue keeps the reasons.
describe("checkReasonText", () => {
  it("has words for a reason it does not know", () => {
    expect(checkReasonText("new_rule")).toBe("new rule");
  });
});

describe("lowResolutionText", () => {
  it("gives the exact warning for a low-resolution page", () => {
    // Changed on purpose (UI quality council, 15 Oct 2026): no DPI on user screens.
    expect(lowResolutionText({ low_resolution: true, effective_dpi: 96.4 })).toBe(
      "This page was scanned at low quality. Small text may be misread. Upload a sharper scan if values look wrong.",
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

describe("untilRead (QA, 15 Oct 2026)", () => {
  it("says all clear and no related documents only once the document is read", () => {
    expect(untilRead("ready", ALL_CLEAR)).toBe(ALL_CLEAR);
    for (const status of ["received", "clean"] as const) {
      expect(untilRead(status, ALL_CLEAR)).toBe("Not read yet. This fills in once the document is read.");
    }
  });

  it("never promises a fill-in for a document that was not read", () => {
    for (const status of ["needs_reader", "failed", "rejected"] as const) {
      expect(untilRead(status, ALL_CLEAR)).toBe("This document was not read, so there is nothing to show.");
    }
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
    // Changed on purpose (UI quality council, 15 Oct 2026): plain words, and what may fix it.
    expect(noCardText("ready")).toBe(
      "No document card. The AI could not make one when this was read. Reading it again may fix this.",
    );
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
    expect(attentionPages([page(1), page(2)], [])).toEqual([]);
    expect(ALL_CLEAR).toBe("Everything on these pages was read clearly.");
  });

  it("lists every problem by page, in page order, in the issue table's words", () => {
    const blurry = { code: "blurry" as const, says: "This page is blurry.", fix: "Scan it again." };
    const pages = [
      page(1),
      page(2, { read_quality: "unreadable", read_issues: ["blurry"], problems: [blurry] }),
      page(3, { read_quality: "uncertain", unsure_words: 14, unpinned_lines: 1, accepted_at: "2026-10-09T10:00:00Z" }),
    ];
    // Changed on purpose (#443): the count is toSettle's, so a page no longer counts its marks here.
    expect(attentionPages(pages, [])).toEqual([
      { pageNo: 2, lines: ["This page is blurry."], kept: false },
      { pageNo: 3, lines: ["14 words unsure", "The exact spot of 1 line needs a check."], kept: true },
    ]);
  });

  it("says an unreadable page with no problems listed needs a closer look", () => {
    expect(attentionPages([page(4, { read_quality: "unreadable", illegible_words: 3 })], [])).toEqual([
      { pageNo: 4, lines: ["This page needs a closer look.", "Hard to read: 3 words."], kept: false },
    ]);
  });

  // Board 2: the card with the Needs review dashed edge (hatched until the MonoEdge design system, 16 Oct 2026), "N to check" and a lead line.
  const attention = (pages: PageSummary[], open: { page_no: number }[] = []) =>
    renderToStaticMarkup(
      createElement(AttentionPanel, {
        status: "ready",
        pages,
        open: open.map((word) => ({ ...word, text: "", reason_text: "" })),
        current: null,
        marks: [],
        onPage: () => undefined,
        onMark: () => undefined,
        onCheck: () => undefined,
      }),
    );

  it("counts what is left to check, with a dashed edge and a lead line in the right number", () => {
    // Changed on purpose (#443): the count is what is left to settle (toSettle): here two words still
    // open on a page whose one problem (lines not pinned) they are; its unsure words are no review item.
    const unpinned = { code: "unpinned" as const, says: "The exact spot of 2 lines needs a check.", fix: "Confirm." };
    const pinned = {
      read_quality: "uncertain" as const,
      read_issues: ["unpinned" as const], // the server's codes, which toSettle reads (#443)
      unsure_words: 2,
      unpinned_lines: 2,
      problems: [unpinned],
    };
    const two = attention([page(1), page(2, pinned)], [{ page_no: 2 }, { page_no: 2 }]);
    expect(two).toMatch(/<section class="[^"]*_attentionToCheck_/);
    expect(two).toContain("badge badge-review");
    expect(two).toContain("2 to check");
    expect(two).toContain("2 reading problems on page 2. Check them on the page before you rely on them.");
    const one = attention([page(3, { read_quality: "uncertain", unsure_words: 1 })]);
    expect(one).toContain("1 to check");
    expect(one).toContain("1 reading problem on page 3. Check it on the page before you rely on it.");
    const many = attention([
      page(1, { read_quality: "uncertain", unsure_words: 1 }),
      page(2, { read_quality: "unreadable" }),
    ]);
    expect(many).toContain("2 reading problems on 2 pages. Check them on the pages before you rely on them.");
  });

  it("shows no count, edge or lead line once every problem page is kept as is, or all is clear", () => {
    const kept = attention([page(1, { read_quality: "uncertain", unsure_words: 4, accepted_at: "2026-10-09T10:00:00Z" })]);
    const clear = attention([page(1)]);
    for (const html of [kept, clear]) {
      expect(html).not.toContain("_attentionToCheck_");
      expect(html).not.toContain("to check");
      expect(html).not.toContain("reading problem");
    }
    expect(clear).toContain(ALL_CLEAR);
  });
});

describe("the note under a highlighted page (board 2)", () => {
  const note = (highlightNote?: string) =>
    renderToStaticMarkup(
      createElement(PageViewer, {
        document: { id: "d1", display_name: "Invoice.jpg", detected_type: "jpeg" },
        page: {
          page_no: 1,
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
        },
        highlights: [box(1)],
        highlightNote,
      }),
    );

  it("names what is highlighted when told, and keeps the old words when not", () => {
    expect(note("Highlighted: Grand total, from the link you opened")).toContain(
      "Highlighted: Grand total, from the link you opened",
    );
    expect(note()).toContain("The highlighted box marks the spot on this page.");
  });
});
