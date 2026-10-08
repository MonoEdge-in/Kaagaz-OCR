// The page viewer's small decisions, kept out of the components so they are
// unit tested in node (tests/viewer-*.test.ts): zoom steps, which rows and
// columns of a sheet to fetch and show, how a page is drawn, and how it was read.

import type { DocumentSummary, PageRoute, PageSummary, SheetCells } from "@/lib/types";

export const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3] as const;

/**
 * When the document screen's page column sticks beside the panels, and fits
 * the whole page in the window ("Fit page"). DocumentView.module.css uses this
 * same media query (tests/layout-guards.test.ts checks they match).
 */
export const STICKY_VIEWER_QUERY = "(min-width: 1024px) and (min-height: 36rem)";

/** The next zoom step in or out; stays put at either end. */
export function stepZoom(current: number, direction: "in" | "out"): number {
  const index = ZOOM_STEPS.findIndex((step) => step >= current - 1e-9);
  const at = index === -1 ? ZOOM_STEPS.length - 1 : index;
  const exact = Math.abs((ZOOM_STEPS[at] ?? 1) - current) < 1e-9;
  let next: number;
  if (direction === "in") next = exact ? at + 1 : at;
  else next = at - 1;
  const clamped = Math.min(ZOOM_STEPS.length - 1, Math.max(0, next));
  return ZOOM_STEPS[clamped] ?? 1;
}

/**
 * The size a page is drawn at: zoom 1 fits the container's width exactly or,
 * given the container's height too, the whole page inside the container
 * ("Fit page"); other zoom steps scale from there. Whole pixels, never below 1.
 */
export function fitPageSize(
  containerWidth: number,
  aspect: number,
  zoom: number,
  containerHeight?: number,
): { width: number; height: number } {
  const safeAspect = Number.isFinite(aspect) && aspect > 0 ? aspect : 1 / Math.SQRT2;
  let base = Number.isFinite(containerWidth) && containerWidth > 0 ? containerWidth : 1;
  if (containerHeight !== undefined && Number.isFinite(containerHeight) && containerHeight > 0) {
    base = Math.min(base, containerHeight * safeAspect);
  }
  const width = Math.max(1, Math.floor(base * zoom));
  return { width, height: width / safeAspect };
}

export type PageMode = "pdf" | "image" | "sheet" | "none";

/** How a page can be drawn: PDF.js for PDFs, the stored image, a cell grid, or nothing. */
export function pageMode(
  document: Pick<DocumentSummary, "detected_type">,
  page: PageSummary,
  pdfFailed: boolean,
): PageMode {
  if (page.unit === "cells") return "sheet";
  if (document.detected_type === "pdf" && !pdfFailed) return "pdf";
  if (page.has_image) return "image";
  return "none";
}

const ROUTE_WORDS: Record<PageRoute, string> = {
  cells: "Read from spreadsheet cells",
  text_layer: "Read from the PDF text",
  ocr: "Read by OCR", // a scanned page read before PRD 5, which does not say who read it
  blank: "Blank page",
};

/** `read_by` of a page the backup reader read (PRD 5 RT2): RapidOCR, on this server's CPU. */
export const BACKUP_READER = "rapidocr";

/**
 * How a page was read, in words, shown beside its number (PRD 2, R1). A scanned
 * page says who read it (PRD 5 RT2): "Read by Gemini" (`read_by` is the model's ID)
 * or "Read by the backup reader". null when the page has no route, as for a page
 * read before PRD 2: nothing is shown.
 */
export function routeText(page: Pick<PageSummary, "route" | "read_by">): string | null {
  if (page.route === null) return null;
  if (page.route === "ocr" && page.read_by !== null) {
    return page.read_by === BACKUP_READER ? "Read by the backup reader" : "Read by Gemini";
  }
  return ROUTE_WORDS[page.route];
}

// The backend gives at most 500 rows per call; 200 keeps the table quick.
export const SHEET_ROWS_PER_VIEW = 200;

/** The first and last row to fetch: a window centred on the highlighted row, if any. */
export function sheetRowWindow(
  focusRow: number | null,
  rowsPerView: number = SHEET_ROWS_PER_VIEW,
): { rowFrom: number; rowTo: number } {
  if (focusRow === null || focusRow < 1) return { rowFrom: 1, rowTo: rowsPerView };
  const rowFrom = Math.max(1, focusRow - Math.floor(rowsPerView / 2));
  return { rowFrom, rowTo: rowFrom + rowsPerView - 1 };
}

/** A few rows around one cell, for a small cropped preview. */
export function cropRowWindow(row: number, around = 3): { rowFrom: number; rowTo: number } {
  const rowFrom = Math.max(1, row - around);
  return { rowFrom, rowTo: row + around };
}

export interface GridRow {
  row: number;
  cells: { column: number; text: string }[];
}

/**
 * Lays the fetched cells out as a full grid (empty cells filled in), from
 * column 1 up to the widest row, or only columns around `focusColumn` when a
 * crop asks for them. Rows the server skipped (empty) are skipped here too.
 */
export function buildGrid(
  sheet: SheetCells,
  columnWindow: { from: number; to: number } | null = null,
): { columns: number[]; rows: GridRow[] } {
  let widest = 0;
  for (const row of sheet.rows) {
    for (const cell of row.cells) widest = Math.max(widest, cell.column);
  }
  const from = columnWindow ? Math.max(1, columnWindow.from) : 1;
  const to = columnWindow ? columnWindow.to : widest;
  const columns: number[] = [];
  for (let c = from; c <= to; c += 1) columns.push(c);
  const rows = sheet.rows.map((row) => {
    const byColumn = new Map(row.cells.map((cell) => [cell.column, cell.text]));
    return {
      row: row.row,
      cells: columns.map((column) => ({ column, text: byColumn.get(column) ?? "" })),
    };
  });
  return { columns, rows };
}
