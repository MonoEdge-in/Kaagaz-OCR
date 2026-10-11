import { describe, expect, it } from "vitest";
import { fieldText, labelBadgeClass, labelIcon, labelText, reasonText } from "@/components/viewer/labels";
import { initialSelection, sameRegion, selectValue } from "@/components/viewer/selection";
import {
  BACKUP_ISSUES,
  buildGrid,
  cropRowWindow,
  fitPageSize,
  pageMode,
  routeText,
  sheetRowWindow,
  stepZoom,
} from "@/components/viewer/viewer";
import { encodeRegion } from "@/lib/regions";
import type {
  DocumentCard,
  DocumentDetail,
  ExtractedValue,
  Invoice,
  InvoiceLine,
  PageSummary,
  Region,
} from "@/lib/types";

const pdfPage: PageSummary = {
  page_no: 1,
  width: 595,
  height: 842,
  unit: "pt",
  has_image: true,
  route: "text_layer",
  effective_dpi: null,
  low_resolution: false,
  read_by: "text_layer",
  read_quality: "good",
  read_issues: [],
  unsure_words: 0,
  illegible_words: 0,
  unpinned_lines: 0,
  accepted_by: null,
  accepted_at: null,
  read_again_requested_at: null,
};
const sheetPage: PageSummary = {
  page_no: 1,
  width: 5,
  height: 40,
  unit: "cells",
  has_image: false,
  route: "cells",
  effective_dpi: null,
  low_resolution: false,
  read_by: "cells",
  read_quality: "good",
  read_issues: [],
  unsure_words: 0,
  illegible_words: 0,
  unpinned_lines: 0,
  accepted_by: null,
  accepted_at: null,
  read_again_requested_at: null,
};

describe("stepZoom", () => {
  it("moves one step in and out", () => {
    expect(stepZoom(1, "in")).toBe(1.25);
    expect(stepZoom(1, "out")).toBe(0.75);
  });

  it("stays at either end", () => {
    expect(stepZoom(3, "in")).toBe(3);
    expect(stepZoom(0.5, "out")).toBe(0.5);
  });

  it("snaps a value between steps to the nearest step that way", () => {
    expect(stepZoom(1.1, "in")).toBe(1.25);
    expect(stepZoom(1.1, "out")).toBe(1);
  });
});

describe("fitPageSize", () => {
  it("fits the container's width at zoom 1", () => {
    expect(fitPageSize(600, 0.5, 1)).toEqual({ width: 600, height: 1200 });
  });

  it("scales from the fitted width, in whole pixels", () => {
    expect(fitPageSize(601, 1, 1.25).width).toBe(751);
    expect(fitPageSize(600, 2, 0.5)).toEqual({ width: 300, height: 150 });
  });

  it("falls back safely for a missing size or aspect", () => {
    expect(fitPageSize(0, 1, 1).width).toBe(1);
    expect(fitPageSize(400, 0, 1).height).toBeCloseTo(400 * Math.SQRT2);
  });

  it("fits the whole page inside the container's height too (Fit page)", () => {
    // A portrait page in a wide, short frame: the height decides.
    expect(fitPageSize(800, 0.5, 1, 600)).toEqual({ width: 300, height: 600 });
    // A landscape page in a tall frame: the width still decides.
    expect(fitPageSize(600, 2, 1, 900)).toEqual({ width: 600, height: 300 });
    // Zoom scales from the fitted page.
    expect(fitPageSize(800, 0.5, 2, 600)).toEqual({ width: 600, height: 1200 });
  });

  it("fits the width alone when the height is missing or not a size", () => {
    expect(fitPageSize(600, 0.5, 1, 0)).toEqual({ width: 600, height: 1200 });
    expect(fitPageSize(600, 0.5, 1, Number.NaN)).toEqual({ width: 600, height: 1200 });
  });
});

describe("pageMode", () => {
  it("draws PDFs with PDF.js, then falls back to the stored image", () => {
    expect(pageMode({ detected_type: "pdf" }, pdfPage, false)).toBe("pdf");
    expect(pageMode({ detected_type: "pdf" }, pdfPage, true)).toBe("image");
    expect(pageMode({ detected_type: "pdf" }, { ...pdfPage, has_image: false }, true)).toBe("none");
  });

  it("shows images and sheets their own way", () => {
    expect(pageMode({ detected_type: "png" }, { ...pdfPage, unit: "px" }, false)).toBe("image");
    expect(pageMode({ detected_type: "zip" }, sheetPage, false)).toBe("sheet");
    expect(pageMode({ detected_type: "png" }, { ...pdfPage, has_image: false }, false)).toBe("none");
  });
});

describe("sheet windows", () => {
  it("starts at row 1 with no highlight", () => {
    expect(sheetRowWindow(null)).toEqual({ rowFrom: 1, rowTo: 200 });
  });

  it("centres on the highlighted row", () => {
    expect(sheetRowWindow(450)).toEqual({ rowFrom: 350, rowTo: 549 });
    expect(sheetRowWindow(20)).toEqual({ rowFrom: 1, rowTo: 200 });
  });

  it("keeps a crop to a few rows", () => {
    expect(cropRowWindow(10)).toEqual({ rowFrom: 7, rowTo: 13 });
    expect(cropRowWindow(1)).toEqual({ rowFrom: 1, rowTo: 4 });
  });

  it("fills empty cells and limits columns for a crop", () => {
    const sheet = {
      page_no: 1,
      total_rows: 3,
      rows: [
        { row: 1, cells: [{ column: 1, text: "Name" }, { column: 3, text: "Total" }] },
        { row: 3, cells: [{ column: 2, text: "x" }] },
      ],
    };
    const grid = buildGrid(sheet);
    expect(grid.columns).toEqual([1, 2, 3]);
    expect(grid.rows[0]?.cells.map((c) => c.text)).toEqual(["Name", "", "Total"]);
    expect(grid.rows[1]?.row).toBe(3);
    expect(buildGrid(sheet, { from: -1, to: 2 }).columns).toEqual([1, 2]);
  });
});

describe("routeText (PRD 2, R1; PRD 5 RT2)", () => {
  it("says in words how each page was read", () => {
    expect(routeText({ route: "cells", read_by: "cells", read_issues: [] })).toBe("Read from spreadsheet cells");
    expect(routeText({ route: "text_layer", read_by: "text_layer", read_issues: [] })).toBe(
      "Read from the PDF text",
    );
    expect(routeText({ route: "blank", read_by: null, read_issues: [] })).toBe("Blank page");
  });

  it("names the model that read the page, as the server names it (RT2, model choice MS5)", () => {
    const gemini = { route: "ocr" as const, read_by: "google/gemini-3.1-pro-preview", read_issues: [] };
    expect(routeText({ ...gemini, read_by_name: "Gemini 3.1 Pro" })).toBe("Read by Gemini 3.1 Pro");
    const claude = { route: "ocr" as const, read_by: "anthropic/claude-sonnet-5.5", read_issues: ["blurry" as const] };
    expect(routeText({ ...claude, read_by_name: "Claude Sonnet 5.5" })).toBe("Read by Claude Sonnet 5.5");
  });

  it("never says OCR for a model's page: without a name it is an AI model, by its ID (R4)", () => {
    const gemini = { route: "ocr" as const, read_by: "google/gemini-3.8-flash", read_issues: [] };
    expect(routeText(gemini)).toBe("Read by an AI model (google/gemini-3.8-flash)");
    expect(routeText({ ...gemini, read_by_name: null })).toBe("Read by an AI model (google/gemini-3.8-flash)");
  });

  it("says Read by OCR only for a scanned page no model read", () => {
    expect(routeText({ route: "ocr", read_by: "rapidocr", read_issues: [] })).toBe("Read by OCR");
    expect(routeText({ route: "ocr", read_by: null, read_issues: [] })).toBe("Read by OCR");
  });

  it("says Read by the backup reader for a handed-on page, even with a model's name", () => {
    const page = { route: "ocr" as const, read_by: "rapidocr", read_by_name: "Gemini 3.1 Pro" };
    expect(routeText({ ...page, read_issues: ["read_locally"] })).toBe("Read by the backup reader");
    expect(routeText({ ...page, read_issues: ["reader_refused"] })).toBe("Read by the backup reader");
  });

  it("names no model for a page that was not scanned", () => {
    expect(routeText({ route: "text_layer", read_by: "text_layer", read_by_name: "Gemini", read_issues: [] })).toBe(
      "Read from the PDF text",
    );
  });

  it("says the backup reader only when the main reader handed the page on (Q7, G6)", () => {
    expect(routeText({ route: "ocr", read_by: "rapidocr", read_issues: ["read_locally"] })).toBe(
      "Read by the backup reader",
    );
    expect(routeText({ route: "ocr", read_by: "rapidocr", read_issues: ["reader_refused", "no_text"] })).toBe(
      "Read by the backup reader",
    );
    expect(BACKUP_ISSUES).toEqual(["read_locally", "reader_refused"]);
  });

  it("keeps the old words for a page read with no key, by an old reader, or before PRD 5", () => {
    expect(routeText({ route: "ocr", read_by: "rapidocr", read_issues: [] })).toBe("Read by OCR");
    expect(routeText({ route: "ocr", read_by: "rapidocr", read_issues: ["low_resolution"] })).toBe("Read by OCR");
    expect(routeText({ route: "ocr", read_by: "an_old_reader", read_issues: [] })).toBe("Read by OCR");
    expect(routeText({ route: "ocr", read_by: null, read_issues: [] })).toBe("Read by OCR");
  });

  it("gives nothing for a page with no route, as for one read before PRD 2", () => {
    expect(routeText({ route: null, read_by: null, read_issues: [] })).toBeNull();
  });
});

describe("labels in words", () => {
  it("says the label and reason, never only a colour", () => {
    expect(labelText("verified")).toBe("Verified");
    expect(labelText("needs_review")).toBe("Needs review");
    expect(reasonText("readers_disagree")).toMatch(/different/);
    expect(fieldText("invoice_number")).toBe("Invoice number");
  });

  it("gives each label its own shape and icon", () => {
    expect(labelBadgeClass("verified")).toBe("badge badge-verified");
    expect(labelBadgeClass("needs_review")).toBe("badge badge-review");
    expect(labelIcon("verified")).toBe("check-seal");
    expect(labelIcon("needs_review")).toBe("eye-alert");
  });
});

describe("reasons for values read from a hard page (PRD 5 V4, G5, Q7)", () => {
  it("says why a value needs review, in words people know, with no engine named", () => {
    expect(reasonText("unsure_text")).toBe("Part of this value was hard to read. Check it against the page.");
    expect(reasonText("box_unanchored")).toBe(
      "The exact spot of this value on the page couldn't be confirmed. Check it against the page.",
    );
    expect(reasonText("local_reader")).toBe(
      "Read by the backup reader while the main reader was down. It will be read again automatically.",
    );
    for (const reason of ["unsure_text", "box_unanchored", "local_reader"] as const) {
      expect(reasonText(reason)).not.toMatch(/gemini|rapidocr|ocr/i);
    }
  });
});

describe("reasons for values the AI model or OCR read (PRD 2)", () => {
  it("asks to check a value whose field the AI model chose (council A1)", () => {
    expect(reasonText("model_chosen")).toBe("Found by the AI model. Check it against the page.");
  });

  it("asks to check a value OCR read from Devanagari or mixed script (council ML 1)", () => {
    expect(reasonText("ocr_script")).toBe(
      "Read by OCR from Hindi, Marathi or mixed-script text. Check it against the page.",
    );
  });
});

describe("the reason for a value an invoice check flagged (PRD 2, K3)", () => {
  it("asks to check the numbers", () => {
    expect(reasonText("check_failed")).toBe("A check on this invoice failed. Check the numbers.");
  });
});

describe("document selection", () => {
  const region: Region = { kind: "box", page: 2, box: [0.1, 0.1, 0.2, 0.2] };
  const value: ExtractedValue = {
    id: "v1",
    document_id: "d1",
    record_key: "document",
    field: "total_amount",
    value: "1,234",
    raw: "1,234",
    second_reading: null,
    page_no: 2,
    region,
    label: "needs_review",
    label_reason: "single_reader",
    confidence: null,
    label_page_no: null, // PRD 2 D3: no label spot of its own
    label_region: null,
  };
  const detail = {
    pages: [pdfPage, { ...pdfPage, page_no: 2 }],
    values: [value],
  };

  it("opens on the first page with no link", () => {
    expect(initialSelection(detail, null, null)).toEqual({ pageNo: 1, region: null, valueId: null });
  });

  it("opens on the linked page and spot, and marks the matching value", () => {
    expect(initialSelection(detail, "2", encodeRegion(region))).toEqual({
      pageNo: 2,
      region,
      valueId: "v1",
    });
  });

  it("lets the region's page win and ignores a broken or foreign region", () => {
    expect(initialSelection(detail, "1", encodeRegion(region))?.pageNo).toBe(2);
    expect(initialSelection(detail, "2", "not-a-region")).toEqual({
      pageNo: 2,
      region: null,
      valueId: null,
    });
    const elsewhere = encodeRegion({ ...region, page: 9 });
    expect(initialSelection(detail, null, elsewhere)).toEqual({
      pageNo: 1,
      region: null,
      valueId: null,
    });
  });

  it("gives null when there are no pages", () => {
    expect(initialSelection({ pages: [], values: [] }, "1", null)).toBeNull();
  });

  it("selects a clicked value on its own page", () => {
    expect(selectValue(value)).toEqual({ pageNo: 2, region, valueId: "v1" });
    expect(sameRegion(region, { kind: "cell", page: 2, row: 1, column: 1 })).toBe(false);
  });
});

describe("the document card's reply shape (PRD 2 D3)", () => {
  it("carries the card, its date and each value's label spot as the API sends them", () => {
    const card: DocumentCard = {
      version: 1,
      run_id: "r1",
      model: "google/gemini-3.1-pro-preview",
      prompt: "f376d271",
      doc_type: "land_record",
      languages: ["mr"],
      title: "A land record",
      summary: null,
      document_date: null,
      parties: [
        {
          role: "owner",
          field: "owner_name",
          name: "सुरेश पाटील",
          name_latin: "suresh patil",
          page: 1,
          region: { kind: "box", page: 1, box: [0.1, 0.2, 0.9, 0.22] },
        },
      ],
      identifiers: [],
      key_facts: [
        {
          field: "profit",
          value: "$12,000",
          page: 16,
          region: { kind: "box", page: 16, box: [0.1, 0.05, 0.9, 0.07] },
          label: "Profit of the month Sept is",
          label_page: 15,
          label_region: { kind: "box", page: 15, box: [0.1, 0.9, 0.9, 0.92] },
        },
      ],
      dropped_quotes: 0,
    };
    const facts: Pick<DocumentDetail, "summary" | "document_date" | "card"> = {
      summary: null,
      document_date: "2026-08-12",
      card,
    };
    const labelled: Pick<ExtractedValue, "label_page_no" | "label_region"> = {
      label_page_no: 15,
      label_region: { kind: "box", page: 15, box: [0.1, 0.9, 0.9, 0.92] },
    };
    expect(facts.card?.parties[0]?.name_latin).toBe("suresh patil");
    expect(labelled.label_page_no).not.toBe(card.key_facts[0]?.page);
  });
});

describe("the detail's invoice (PRD 2 E5)", () => {
  it("carries the invoice row and its lines, amounts as strings, or null", () => {
    const line: InvoiceLine = {
      line_no: 1,
      description: "Cotton Poplin",
      hsn: "5208",
      quantity: "120",
      unit: "m",
      rate: "25.00",
      amount: "3000.00",
      label: "needs_review",
    };
    const invoice: Invoice = {
      invoice_number: "TX-2026-118",
      invoice_date: "2026-08-12",
      seller_name: "Shree Ganesh Textiles",
      seller_gstin: "24ABCDE1234F1Z6",
      buyer_name: "Patil Garments",
      buyer_gstin: null,
      subtotal: "3000.00",
      tax_total: null,
      total: "3000.00",
      currency: "INR",
      label: "needs_review",
      check_status: "passed",
      check_reasons: [],
      lines: [line],
    };
    const detail: Pick<DocumentDetail, "invoice"> = { invoice };
    const none: Pick<DocumentDetail, "invoice"> = { invoice: null };
    expect(detail.invoice?.lines[0]?.amount).toBe("3000.00");
    expect(none.invoice).toBeNull();
  });
});
