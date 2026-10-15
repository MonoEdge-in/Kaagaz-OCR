import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { scannedLink } from "@/components/chat/answer";
import {
  CONFIDENCE_NOTE,
  confidenceText,
  emptyState,
  lineMarked,
  notSure,
  readerHeading,
  routeOrigin,
  secondReaderNote,
  truncatedText,
  valueAt,
  visibleText,
} from "@/components/viewer/scanned";
import { decodeRegion, pageLink } from "@/lib/regions";
import type { Citation, ExtractedValue, PageReader, ReadLine, Region } from "@/lib/types";

const box = (page: number, b: [number, number, number, number]): Region => ({ kind: "box", page, box: b });
const cell = (page: number, row: number, column: number): Region => ({ kind: "cell", page, row, column });

const line = (confidence: number | null, region: Region = box(1, [0.1, 0.1, 0.9, 0.14])): ReadLine => ({
  text: "x",
  confidence,
  origin: "ocr",
  kind: null,
  region,
});

const reader = (overrides: Partial<PageReader> = {}): PageReader => ({
  role: "first",
  reader: "old-reader",
  reader_version: "1.5",
  truncated: false,
  lines: [line(0.97)],
  ...overrides,
});

describe("confidenceText", () => {
  // Changed on purpose (UI quality council, 15 Oct 2026): no percentage, and nothing for a
  // line the reader was sure of; only "Reader not sure" under 0.90.
  it("says not sure under 0.90, with no number, and nothing from 0.90", () => {
    expect(confidenceText(0.9, "ocr")).toBeNull();
    expect(confidenceText(0.97, "ocr")).toBeNull();
    expect(confidenceText(0.97, "cell")).toBeNull();
    expect(confidenceText(0.8999, "ocr")).toBe("Reader not sure");
    expect(confidenceText(0.62, "ocr")).toBe("Reader not sure");
  });

  it("says read from the file for a cell or text layer, else nothing, and treats NaN as no score", () => {
    expect(confidenceText(null, "cell")).toBe("Read directly from the file");
    expect(confidenceText(null, "text_layer")).toBe("Read directly from the file");
    expect(confidenceText(null, "ocr")).toBeNull();
    expect(confidenceText(null, null)).toBeNull();
    expect(confidenceText(Number.NaN, "ocr")).toBeNull();
  });

  it("has one plain note, shown only beside a Reader not sure tag", () => {
    expect(CONFIDENCE_NOTE).toBe("'Not sure' is the reader's own view, not a check.");
    expect(notSure(0.62)).toBe(true);
    expect(notSure(0.9)).toBe(false);
    expect(notSure(Number.NaN)).toBe(false);
    expect(notSure(null)).toBe(false);
    const panel = readFileSync(join(__dirname, "..", "components/viewer/ScannedTextPanel.tsx"), "utf8");
    expect(panel).toContain("{lines.some((line) => notSure(line.confidence)) && <p className={styles.muted}>{CONFIDENCE_NOTE}</p>}");
    const read = readFileSync(join(__dirname, "..", "components/viewer/WhatWasRead.tsx"), "utf8");
    expect(read).toContain("{notSure(value.confidence) && <span className={styles.muted}>{CONFIDENCE_NOTE}</span>}");
    const card = readFileSync(join(__dirname, "..", "components/review/ReviewCard.tsx"), "utf8");
    expect(card).toContain("{notSure(value.confidence) && <p className={`muted ${styles.reason}`}>{CONFIDENCE_NOTE}</p>}");
  });

  it("gives a value the origin of its page's route", () => {
    expect(routeOrigin("cells")).toBe("cell");
    expect(routeOrigin("text_layer")).toBe("text_layer");
    expect(routeOrigin("ocr")).toBe("ocr");
    expect(routeOrigin("blank")).toBeNull();
    expect(routeOrigin(undefined)).toBeNull();
  });
});

describe("readerHeading", () => {
  const ocrPage = { read_issues: [] };

  // Changed on purpose (UI quality council, 15 Oct 2026): no model name or DPI.
  it("says which reader, and how many lines it was not sure of", () => {
    const heading = readerHeading(reader({ lines: [line(0.5), line(0.97), line(0.2)] }), ocrPage);
    expect(heading).toEqual({
      title: "Main reader",
      notSureCount: "2 of 3 lines: reader not sure",
    });
    // PRD 5 U8: no engine's name, whatever the server calls its reader.
    expect(JSON.stringify(heading)).not.toMatch(/old-reader|rapid/i);
    expect(readerHeading(reader(), ocrPage).notSureCount).toBeNull();
    expect(readerHeading(reader({ lines: [line(0.5)] }), ocrPage).notSureCount).toBe(
      "1 of 1 line: reader not sure",
    );
  });

  it("names the backup reader only for a page the main reader handed on (PRD 5 RT2, Q7)", () => {
    expect(readerHeading(reader(), { read_issues: ["read_locally"] }).title).toBe("Backup reader");
    expect(readerHeading(reader(), { read_issues: ["reader_refused", "no_text"] }).title).toBe("Backup reader");
    expect(readerHeading(reader(), { read_issues: ["low_resolution"] }).title).toBe("Main reader");
  });

  it("names the checker as the text inside the PDF", () => {
    expect(readerHeading(reader({ role: "second" }), ocrPage).title).toBe("Checker (text inside the PDF)");
    expect(readerHeading(reader({ role: "second" }), { read_issues: ["read_locally"] }).title).toBe(
      "Checker (text inside the PDF)",
    );
  });

  it("says when the checker did not count", () => {
    expect(secondReaderNote("ocr")).toBe("Not used for this page: it is a scanned page.");
    expect(secondReaderNote("blank")).toBe("Not used for this page: it is blank.");
    expect(secondReaderNote("text_layer")).toBeNull();
    expect(secondReaderNote("cells")).toBeNull();
    expect(secondReaderNote(null)).toBeNull();
  });
});

describe("emptyState", () => {
  it("covers loading, errors and Too many requests", () => {
    expect(emptyState({ status: "loading" }, "ocr")).toEqual({
      text: "Loading the scanned text…",
      retry: false,
    });
    expect(emptyState({ status: "error", httpStatus: 429 }, "ocr")).toEqual({
      text: "Too many requests, try again in a minute.",
      retry: true,
    });
    expect(emptyState({ status: "error", httpStatus: 500 }, "ocr")).toEqual({
      text: "The scanned text could not be loaded. Try again.",
      retry: true,
    });
    expect(emptyState({ status: "error", httpStatus: null }, "ocr")?.text).toBe(
      "The scanned text could not be loaded. Check your connection, then try again.",
    );
  });

  it("says not read yet, blank, or no text found; null when there are lines", () => {
    const empty = reader({ lines: [] });
    expect(emptyState({ status: "ready", reader: undefined }, "ocr")?.text).toBe("Not read yet.");
    expect(emptyState({ status: "ready", reader: empty }, "blank")?.text).toBe("Blank page.");
    expect(emptyState({ status: "ready", reader: empty }, "ocr")?.text).toBe(
      "No text was found on this page. Upload a sharper scan.",
    );
    expect(emptyState({ status: "ready", reader: empty }, "text_layer")?.text).toBe(
      "No text was found on this page.",
    );
    expect(emptyState({ status: "ready", reader: reader() }, "ocr")).toBeNull();
  });

  it("never asks for a sharper scan when the checker found nothing", () => {
    const second = reader({ role: "second", lines: [] });
    expect(emptyState({ status: "ready", reader: second }, "ocr")?.text).toBe(
      "No text was found on this page.",
    );
  });

  it("says when a reader's lines were cut short", () => {
    expect(truncatedText(reader())).toBeNull();
    const many = Array.from({ length: 2000 }, () => line(0.9));
    expect(truncatedText(reader({ truncated: true, lines: many }))).toBe(
      "Showing the first 2,000 lines. The rest of this page was left out.",
    );
    expect(truncatedText(reader({ truncated: true }))).toBe(
      "Showing the first 1 line. The rest of this page was left out.",
    );
  });
});

describe("visibleText", () => {
  it("keeps markup literal and Devanagari joiners as they are", () => {
    expect(visibleText("<script>alert(1)</script>")).toBe("<script>alert(1)</script>");
    expect(visibleText("क्\u200Dष र\u200C्")).toBe("क्\u200Dष र\u200C्");
    expect(visibleText("कुल ₹ 1,200")).toBe("कुल ₹ 1,200");
  });

  it("keeps line breaks, with CR LF and CR as LF", () => {
    expect(visibleText("a\nb")).toBe("a\nb");
    expect(visibleText("a\r\nb")).toBe("a\nb");
    expect(visibleText("a\rb")).toBe("a\nb");
    expect(visibleText("a\u000Bb")).toBe("a⟦U+000B⟧b");
  });

  it("marks controls and direction marks, and shows a tab as a space", () => {
    expect(visibleText("abc\u202Edef")).toBe("abc⟦U+202E⟧def");
    expect(visibleText("a\tb")).toBe("a b");
    expect(visibleText("\u0000\u0085\u200E\u200F\u061C\u2066\uFEFF")).toBe(
      "⟦U+0000⟧⟦U+0085⟧⟦U+200E⟧⟦U+200F⟧⟦U+061C⟧⟦U+2066⟧⟦U+FEFF⟧",
    );
  });
});

describe("lineMarked", () => {
  const union = box(2, [0.1, 0.2, 0.9, 0.5]); // a citation's box over two lines

  it("marks a line whose centre is inside the cited box, on its page only", () => {
    expect(lineMarked(line(null, box(2, [0.1, 0.2, 0.9, 0.25])), union)).toBe(true);
    expect(lineMarked(line(null, box(2, [0.1, 0.45, 0.9, 0.5])), union)).toBe(true);
    expect(lineMarked(line(null, box(2, [0.1, 0.6, 0.9, 0.65])), union)).toBe(false);
    expect(lineMarked(line(null, box(1, [0.1, 0.2, 0.9, 0.25])), union)).toBe(false);
    expect(lineMarked(line(null, box(2, [0.1, 0.2, 0.9, 0.25])), null)).toBe(false);
  });

  it("marks the same cell, never another cell or a box", () => {
    expect(lineMarked(line(null, cell(1, 3, 2)), cell(1, 3, 2))).toBe(true);
    expect(lineMarked(line(null, cell(1, 3, 1)), cell(1, 3, 2))).toBe(false);
    expect(lineMarked(line(null, cell(1, 3, 2)), box(1, [0, 0, 1, 1]))).toBe(false);
  });
});

describe("links to the scanned text", () => {
  it("opens the panel with ?scanned=1", () => {
    expect(pageLink("doc-1", 2, undefined, { scanned: true })).toBe("/documents/doc-1?page=2&scanned=1");
    expect(pageLink("doc-1", 2)).toBe("/documents/doc-1?page=2");
  });

  const citation = (overrides: Partial<Citation> = {}): Citation => ({
    n: 1,
    kind: "piece",
    ref_id: "p1",
    document_id: "doc-1",
    document_name: "Invoice.pdf",
    detected_type: "pdf",
    page_no: 2,
    regions: [box(2, [0.1, 0.2, 0.3, 0.4])],
    snippet: "Total 1,234",
    label: "verified",
    ...overrides,
  });

  it("gives live and older sources a See scanned text link at the cited spot", () => {
    for (const source_state of ["live", "older_version", undefined] as const) {
      const link = scannedLink(citation({ source_state }));
      expect(link).not.toBeNull();
      const params = new URL(link ?? "", "http://x").searchParams;
      expect([params.get("page"), params.get("scanned")]).toEqual(["2", "1"]);
      expect(decodeRegion(params.get("region"))).toEqual(box(2, [0.1, 0.2, 0.3, 0.4]));
    }
  });

  it("gives no scanned-text action to a gone source or a sheet cell", () => {
    expect(scannedLink(citation({ source_state: "in_trash", regions: [], snippet: null }))).toBeNull();
    expect(scannedLink(citation({ source_state: "deleted", regions: [], snippet: null }))).toBeNull();
    // Even if a gone source still carried a spot, it gets no link.
    expect(scannedLink(citation({ source_state: "in_trash" }))).toBeNull();
    expect(scannedLink(citation({ regions: [cell(2, 1, 1)] }))).toBeNull();
  });

  it("finds the value at a card or invoice spot", () => {
    const value = { id: "v1", region: box(1, [0.1, 0.2, 0.3, 0.25]) } as ExtractedValue;
    expect(valueAt([value], box(1, [0.1, 0.2, 0.3, 0.25]))?.id).toBe("v1");
    expect(valueAt([value], box(1, [0.1, 0.3, 0.3, 0.35]))).toBeUndefined();
  });
});
