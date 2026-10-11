// The page viewer's small decisions, kept out of the components so they are
// unit tested in node (tests/viewer-*.test.ts): zoom steps, which rows and
// columns of a sheet to fetch and show, how a page is drawn, and how it was read.

import type { DocumentSummary, PageRoute, PageSummary, ReadIssue, SheetCells } from "@/lib/types";

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

const FOCUS_TEXT_PX = 24; // a highlighted spot is zoomed until it is about this tall
const FOCUS_ROOM = 0.9; // and never past 90% of the frame, across or down

/**
 * The zoom a highlighted spot is shown at: enough to make it about 24px tall,
 * kept inside 90% of the frame (`frame.height` Infinity when the frame grows
 * with the page), snapped down to a zoom step, never below 1 (a long line
 * stays fitted) or above 3. `spot` is its size in pixels at zoom 1.
 */
export function focusZoom(
  spot: { width: number; height: number },
  frame: { width: number; height: number },
): number {
  if (!(spot.width > 0 && spot.height > 0 && frame.width > 0 && frame.height > 0)) return 1;
  const wanted = Math.min(
    FOCUS_TEXT_PX / spot.height,
    (FOCUS_ROOM * frame.width) / spot.width,
    (FOCUS_ROOM * frame.height) / spot.height,
  );
  return ZOOM_STEPS.reduce<number>((best, step) => (step <= wanted && step > best ? step : best), 1);
}

/**
 * Where to scroll a frame (`frame` its visible size, `stage` all it scrolls
 * over) so a spot (in the stage's pixels) sits in its middle, kept in range.
 */
export function centerScroll(
  spot: { left: number; top: number; width: number; height: number },
  stage: { width: number; height: number },
  frame: { width: number; height: number },
): { left: number; top: number } {
  const center = (start: number, size: number, all: number, seen: number) =>
    Math.min(Math.max(0, start + size / 2 - seen / 2), Math.max(0, all - seen));
  return {
    left: center(spot.left, spot.width, stage.width, frame.width),
    top: center(spot.top, spot.height, stage.height, frame.height),
  };
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
  ocr: "Read by OCR", // a scanned page no model read (no key, or before PRD 5)
  blank: "Blank page",
};

/**
 * The issues of a page the main reader handed to the backup reader (PRD 5 Q7, G6):
 * read while it was down, or after it declined the page or sent a bad reply.
 */
export const BACKUP_ISSUES: readonly ReadIssue[] = ["read_locally", "reader_refused"];

/**
 * How a page was read, in words, shown beside its number (PRD 2, R1). A scanned
 * page says who read it (PRD 5 RT2): "Read by the backup reader" when the main
 * reader handed it on (`read_issues` has one of BACKUP_ISSUES), "Read by
 * {read_by_name}" when a model read it (model choice: the server names it),
 * "Read by an AI model ({read_by})" when a model the catalogue does not name
 * read it (never "OCR" for a model's page), and otherwise "Read by OCR", as
 * before PRD 5 (a page read with no key, or before PRD 5). null when the page
 * has no route, as for a page read before PRD 2: nothing is shown.
 */
export function routeText(
  page: Pick<PageSummary, "route" | "read_by" | "read_by_name" | "read_issues">,
): string | null {
  if (page.route === null) return null;
  if (page.route === "ocr") {
    if (page.read_issues.some((issue) => BACKUP_ISSUES.includes(issue))) return "Read by the backup reader";
    if (page.read_by_name) return `Read by ${page.read_by_name}`;
    // An OpenRouter model ID is author/slug; rapidocr and older readers have no slash.
    if (page.read_by?.includes("/")) return `Read by an AI model (${page.read_by})`;
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
