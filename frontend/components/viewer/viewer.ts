// The page viewer's small decisions, kept out of the components so they are
// unit tested in node (tests/viewer-*.test.ts): zoom steps, which rows and
// columns of a sheet to fetch and show, and how a page is drawn.

import type { DocumentSummary, PageSummary, SheetCells } from "@/lib/types";

export const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3] as const;

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
