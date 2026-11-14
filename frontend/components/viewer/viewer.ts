// The page viewer's small decisions, kept out of the components so they are
// unit tested in node (tests/viewer-*.test.ts): zoom steps, which rows and
// columns of a sheet to fetch and show, how a page is drawn, and how it was read.

import type { DetectedType, DocumentSummary, PageSummary, ReadIssue, Region, SheetCells } from "@/lib/types";
import type { ViewRect } from "./canvasPlan";
import { sameRegion } from "./selection";

export const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3] as const;

/**
 * When the document screen's page column sticks beside the panels, as tall as
 * <main>, the page fitted to its width at first (UI audit DD5). DocumentView.module.css
 * uses this same media query (tests/layout-guards.test.ts checks they match).
 */
export const STICKY_VIEWER_QUERY = "(min-width: 1024px) and (min-height: 36rem)";

/**
 * The wheel over the page (UI audit DD1, 16 Oct 2026): with Ctrl or Cmd held (a trackpad
 * pinch sends ctrlKey too) it zooms; otherwise null, and the wheel scrolls as usual, on
 * into <main> once the page has nothing left to scroll.
 */
export function wheelZoom(event: Pick<WheelEvent, "ctrlKey" | "metaKey" | "deltaY">): "in" | "out" | null {
  if (!(event.ctrlKey || event.metaKey) || event.deltaY === 0) return null;
  return event.deltaY < 0 ? "in" : "out";
}

/** The page column's share of the document screen, in percent (DD5's split), and its key step. */
// start 55 (Tushar, 16 Oct 2026): the panel's four tabs fit beside the page at 1280px.
export const SPLIT = { min: 30, max: 75, start: 55, step: 5 } as const;
/** A press on the split's handle becomes a drag only past this many pixels (brief B: 4-5px). */
export const DRAG_THRESHOLD_PX = 5;

/** The page column's share after a drag of `dx` px across a layout `width` px wide, from `start`; in range. */
export function splitAfterDrag(start: number, dx: number, width: number): number {
  const moved = width > 0 ? (dx / width) * 100 : 0;
  return Math.round(Math.min(SPLIT.max, Math.max(SPLIT.min, start + moved)));
}

/** The share after a key on the handle (Left/Right a step, Home/End the ends); null for other keys. */
export function splitAfterKey(current: number, key: string): number | null {
  if (key === "ArrowLeft") return Math.max(SPLIT.min, current - SPLIT.step);
  if (key === "ArrowRight") return Math.min(SPLIT.max, current + SPLIT.step);
  if (key === "Home") return SPLIT.min;
  if (key === "End") return SPLIT.max;
  return null;
}

/**
 * Document panel UX (25 Oct 2026): how many of the panel's tabs fit a tab row `rem` wide
 * beside More, four at most, two at least (More is then its icon alone); `long` when Needs
 * attention, the longest tab, is among them. Four until the row is measured.
 */
export function tabsThatFit(rem: number | null, long: boolean): number {
  if (rem === null) return 4;
  // Changed on purpose (24 Oct 2026): the tabs are targets with their own padding (#301), about
  // 1rem wider each, so a row of four needs about 4rem more and a tab never shows cut off.
  const [four, three] = long ? [32, 25] : [28, 21];
  return rem >= four ? 4 : rem >= three ? 3 : 2;
}

/**
 * The PDF viewer's hand (Vrushit UAT 2, 27 Oct 2026, #419): where a drag that began at `press`
 * (the pointer, and the pages' scroll then) scrolls the pages with the pointer now at x, y. The
 * page follows the pointer, so the scroll moves the other way; the browser keeps it in range.
 */
export function panTo(
  press: { x: number; y: number; left: number; top: number },
  x: number,
  y: number,
): { left: number; top: number } {
  return { left: press.left - (x - press.x), top: press.top - (y - press.y) };
}

/**
 * The page most in view between `top` and `bottom`, by the share of it shown, the first of a tie:
 * the PDF engine's own rule for its current page (#454, review panel). null when none is in view.
 */
export function mostVisiblePage(
  pages: readonly { page: number; top: number; bottom: number }[],
  top: number,
  bottom: number,
): number | null {
  let best: { page: number; shown: number } | null = null;
  for (const each of pages) {
    const shown = (Math.min(each.bottom, bottom) - Math.max(each.top, top)) / (each.bottom - each.top);
    if (shown > 0 && (!best || shown > best.shown || (shown === best.shown && each.page < best.page))) best = { page: each.page, shown };
  }
  return best?.page ?? null;
}

type Rect = Pick<DOMRectReadOnly, "left" | "top" | "width" | "height">;
type ScrollArea = Pick<Element, "scrollLeft" | "scrollTop" | "clientLeft" | "clientTop" | "clientWidth" | "clientHeight">;

/**
 * The scroll that puts a drawn rect's centre in the middle of a scroll area (#446), both in
 * screen pixels (getBoundingClientRect), the area's own `box` taken with its borders. The browser
 * keeps it in range, so a spot near a page's edge goes as far towards the middle as it can.
 */
export function centredScroll(rect: Rect, box: Pick<Rect, "left" | "top">, area: ScrollArea): { left: number; top: number } {
  return {
    left: area.scrollLeft + rect.left + rect.width / 2 - (box.left + area.clientLeft + area.clientWidth / 2),
    top: area.scrollTop + rect.top + rect.height / 2 - (box.top + area.clientTop + area.clientHeight / 2),
  };
}

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
  return { width, height: Math.max(1, Math.floor(width / safeAspect)) };
}

/** A box on screen, in CSS pixels from the window's top left (getBoundingClientRect). */
export interface ScreenBox {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/**
 * The part of a zoomed page that shows (inside the stage, its scrolling frame
 * and the window), in CSS pixels on the page, rounded outwards to whole pixels;
 * null when none of it shows. The sharp zoom overlay draws only this part.
 */
export function visibleRect(
  stage: ScreenBox,
  frame: ScreenBox,
  viewport: ScreenBox,
): ViewRect | null {
  const pageWidth = stage.right - stage.left;
  const pageHeight = stage.bottom - stage.top;
  const x0 = Math.max(0, Math.floor(Math.max(frame.left, viewport.left) - stage.left));
  const y0 = Math.max(0, Math.floor(Math.max(frame.top, viewport.top) - stage.top));
  const x1 = Math.min(pageWidth, Math.ceil(Math.min(frame.right, viewport.right) - stage.left));
  const y1 = Math.min(pageHeight, Math.ceil(Math.min(frame.bottom, viewport.bottom) - stage.top));
  return x1 > x0 && y1 > y0 ? { x: x0, y: y0, width: x1 - x0, height: y1 - y0 } : null;
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

/**
 * Whether the document screen scrolls its page into view for a spot picked in
 * a list (stacked on a phone, the page is above it). A new box on the page:
 * no, PageViewer scrolls the spot itself (one scroll, not two). A spreadsheet
 * cell, or the spot already shown (the user has scrolled away since): yes.
 */
export function bringPageForSpot(shown: Region | null, next: Region): boolean {
  return next.kind === "cell" || (shown !== null && sameRegion(shown, next));
}

export type PageMode = "pdf" | "image" | "sheet" | "none";

// #522 PR 2: a Word or PowerPoint file was read from the PDF the server made of it, which its
// file address serves the viewer (Download still gets the original).
const PDF_VIEWED: ReadonlySet<DetectedType> = new Set<DetectedType>(["pdf", "docx", "dotx", "doc", "pptx", "ppt"]);

/** Whether a document's pages are a PDF's, drawn by PDF.js: a PDF, or a Word or PowerPoint file. */
export function viewsAsPdf(detectedType: DetectedType): boolean {
  return PDF_VIEWED.has(detectedType);
}

/** How a page can be drawn: PDF.js for PDFs, the stored image, a cell grid, or nothing. */
export function pageMode(
  document: Pick<DocumentSummary, "detected_type">,
  page: PageSummary,
  pdfFailed: boolean,
): PageMode {
  if (page.unit === "cells") return "sheet";
  if (viewsAsPdf(document.detected_type) && !pdfFailed) return "pdf";
  if (page.has_image) return "image";
  return "none";
}

/**
 * The issues of a page the main reader handed to the backup reader (PRD 5 Q7, G6):
 * read while it was down, or after it declined the page or sent a bad reply.
 */
export const BACKUP_ISSUES: readonly ReadIssue[] = ["read_locally", "reader_refused"];

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

// --- Page turns (#589): kept in this browser per person and file, as List or Grid is
// (components/files/viewMode.ts). The file's id is its version's, so a new version starts with
// none. Storage can be blocked (a private window) or full, so every read and write is in
// try/catch: then the turns last for this visit only.

export const pageTurnsKey = (userId: string, documentId: string) => `kaagaz.page-turns:${userId}:${documentId}`;

/** This person's turns of this file: page index to quarter turns right, 1 to 3; none when unreadable. */
export function readPageTurns(userId: string, documentId: string): Map<number, number> {
  const turns = new Map<number, number>();
  try {
    const kept: unknown = JSON.parse(localStorage.getItem(pageTurnsKey(userId, documentId)) ?? "{}");
    if (typeof kept !== "object" || kept === null) return turns;
    for (const [page, turn] of Object.entries(kept)) {
      if (/^\d+$/.test(page) && (turn === 1 || turn === 2 || turn === 3)) turns.set(Number(page), turn);
    }
  } catch {
    // unreadable: the file opens unturned
  }
  return turns;
}

/** Keep this person's turns of this file; none left removes the entry. */
export function savePageTurns(userId: string, documentId: string, turns: ReadonlyMap<number, number>): void {
  try {
    const key = pageTurnsKey(userId, documentId);
    if (turns.size === 0) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(Object.fromEntries(turns)));
  } catch {
    // Storage blocked or full: the turns last for this visit only.
  }
}
