import { describe, expect, it } from "vitest";
import { fieldText, labelBadgeClass, labelIcon, labelText, reasonText } from "@/components/viewer/labels";
import { initialSelection, sameRegion, selectValue } from "@/components/viewer/selection";
import {
  BACKUP_ISSUES,
  ZOOM_STEPS,
  bringPageForSpot,
  buildGrid,
  centerScroll,
  cropRowWindow,
  fitPageSize,
  focusZoom,
  pageMode,
  sheetRowWindow,
  stepZoom,
  visibleRect,
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
    // Changed on purpose (UI quality council, 15 Oct 2026): the height is whole pixels too (565.69 -> 565).
    expect(fitPageSize(400, 0, 1).height).toBe(565);
  });

  it("rounds the height down to whole pixels, so the stage never ends on half a pixel", () => {
    expect(fitPageSize(595, 595 / 842, 1)).toEqual({ width: 595, height: 842 });
    expect(fitPageSize(601, 1 / Math.SQRT2, 1)).toEqual({ width: 601, height: 849 }); // 849.94
    expect(fitPageSize(1, 100, 1).height).toBe(1);
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

describe("visibleRect (what the sharp zoom overlay draws)", () => {
  const screen = { left: 0, top: 0, right: 1440, bottom: 900 };

  it("is the part of the page inside the frame, in page pixels", () => {
    // A 2000 x 2828 page scrolled 300 across and 500 down in a frame at (100, 50).
    const stage = { left: -200, top: -450, right: 1800, bottom: 2378 };
    const frame = { left: 100, top: 50, right: 900, bottom: 850 };
    expect(visibleRect(stage, frame, screen)).toEqual({ x: 300, y: 500, width: 800, height: 800 });
  });

  it("is cut to the window when the frame runs past it", () => {
    const stage = { left: 0, top: 0, right: 2000, bottom: 2828 };
    const frame = { left: 0, top: 600, right: 1000, bottom: 1600 };
    expect(visibleRect(stage, frame, screen)).toEqual({ x: 0, y: 600, width: 1000, height: 300 });
  });

  it("rounds outwards and stays on the page", () => {
    const stage = { left: 10.6, top: 20.4, right: 410.6, bottom: 585.4 };
    const frame = { left: 0, top: 0, right: 300.2, bottom: 2000 };
    // Left of the page: from 0. Right edge 289.6 -> 290. Down to the page's end (565).
    expect(visibleRect(stage, frame, screen)).toEqual({ x: 0, y: 0, width: 290, height: 565 });
  });

  it("is null when none of the page shows", () => {
    const stage = { left: 0, top: 1000, right: 500, bottom: 1700 };
    expect(visibleRect(stage, { left: 0, top: 0, right: 500, bottom: 2000 }, screen)).toBeNull();
  });
});

describe("bringPageForSpot (one scroll to a picked spot, not two)", () => {
  const box: Region = { kind: "box", page: 1, box: [0.1, 0.2, 0.3, 0.25] };
  const other: Region = { kind: "box", page: 1, box: [0.5, 0.6, 0.7, 0.65] };
  const cell: Region = { kind: "cell", page: 1, row: 4, column: 2 };

  it("leaves a new box to PageViewer, which scrolls the spot itself", () => {
    expect(bringPageForSpot(null, box)).toBe(false);
    expect(bringPageForSpot(other, box)).toBe(false);
  });

  it("brings the page for a cell, or for the spot already shown", () => {
    expect(bringPageForSpot(null, cell)).toBe(true);
    expect(bringPageForSpot(box, cell)).toBe(true);
    expect(bringPageForSpot(box, { ...box })).toBe(true);
  });
});

describe("focusZoom (a highlighted spot shown big enough to read)", () => {
  const frame = { width: 800, height: 600 };

  it("zooms a word 2x or 3x, so it is about 24px tall", () => {
    expect(focusZoom({ width: 40, height: 10 }, frame)).toBe(2); // 2.4 snaps down to 2
    expect(focusZoom({ width: 30, height: 8 }, frame)).toBe(3);
  });

  it("keeps a line 80% of the frame wide fitted", () => {
    expect(focusZoom({ width: 640, height: 10 }, frame)).toBe(1);
  });

  it("never goes past 3, and snaps down to a zoom step", () => {
    expect(focusZoom({ width: 4, height: 1 }, frame)).toBe(3);
    const zoom = focusZoom({ width: 40, height: 15 }, frame); // 1.6
    expect(zoom).toBe(1.5);
    expect(ZOOM_STEPS).toContain(zoom);
  });

  it("keeps the spot inside the frame", () => {
    // 2x would make it 24px tall, but the frame is 20px tall: 0.9 * 20 / 12 = 1.5.
    expect(focusZoom({ width: 50, height: 12 }, { width: 800, height: 20 })).toBe(1.5);
    // A frame that grows with the page: only its width limits.
    expect(focusZoom({ width: 40, height: 10 }, { width: 800, height: Infinity })).toBe(2);
  });

  it("stays at 1 for a spot or frame with no size", () => {
    expect(focusZoom({ width: 0, height: 10 }, frame)).toBe(1);
    expect(focusZoom({ width: 40, height: 0 }, frame)).toBe(1);
    expect(focusZoom({ width: Number.NaN, height: 10 }, frame)).toBe(1);
    expect(focusZoom({ width: 40, height: 10 }, { width: 0, height: 600 })).toBe(1);
    expect(focusZoom({ width: 40, height: 10 }, { width: 800, height: Number.NaN })).toBe(1);
  });
});

describe("centerScroll", () => {
  const stage = { width: 2000, height: 3000 };
  const frame = { width: 600, height: 400 };

  it("puts the spot in the middle of the frame", () => {
    expect(centerScroll({ left: 500, top: 400, width: 100, height: 20 }, stage, frame)).toEqual({ left: 250, top: 210 });
  });

  it("stays in range at both edges", () => {
    expect(centerScroll({ left: 10, top: 10, width: 20, height: 10 }, stage, frame)).toEqual({ left: 0, top: 0 });
    expect(centerScroll({ left: 1950, top: 2980, width: 40, height: 10 }, stage, frame)).toEqual({
      left: 1400,
      top: 2600,
    });
  });

  it("does not scroll a frame bigger than the stage", () => {
    expect(centerScroll({ left: 200, top: 250, width: 40, height: 10 }, { width: 300, height: 300 }, frame)).toEqual({
      left: 0,
      top: 0,
    });
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

// Changed on purpose (UI quality council, 15 Oct 2026): the page toolbar no longer says how
// or by which model a page was read, so routeText and its tests are gone; scanned.ts still
// names the backup reader from BACKUP_ISSUES.
describe("BACKUP_ISSUES (PRD 5 Q7, G6)", () => {
  it("are the issues of a page the main reader handed to the backup reader", () => {
    expect(BACKUP_ISSUES).toEqual(["read_locally", "reader_refused"]);
  });
});

describe("labels in words", () => {
  it("says the label and reason, never only a colour", () => {
    expect(labelText("verified")).toBe("Verified");
    expect(labelText("needs_review")).toBe("Needs review");
    expect(reasonText("readers_disagree")).toMatch(/different/);
    expect(fieldText("invoice_number")).toBe("Invoice number");
  });

  it("keeps tax and bank short forms in capitals in a field's name", () => {
    expect(fieldText("seller_gstin")).toBe("Seller GSTIN");
    expect(fieldText("ifsc")).toBe("IFSC");
    expect(fieldText("cgst_amount")).toBe("CGST amount");
    expect(fieldText("tax_total")).toBe("Tax total");
    expect(fieldText("pancake_count")).toBe("Pancake count");
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
      "Check the exact spot of this value against the page.",
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
    // Changed on purpose (UI quality council, 15 Oct 2026): "the AI", not "the AI model".
    expect(reasonText("model_chosen")).toBe("Found by the AI. Check it against the page.");
  });

  it("asks to check a value OCR read from Devanagari or mixed script (council ML 1)", () => {
    // Changed on purpose (UI quality council, 15 Oct 2026): no "OCR" on user screens.
    expect(reasonText("ocr_script")).toBe(
      "Read from a scan in Hindi, Marathi or mixed script. Check it against the page.",
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
