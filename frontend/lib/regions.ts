// Where a value sits on its page, turned into what the screen needs: pixel
// boxes over a drawn page, a padded crop around one spot, and a short string
// that carries a region in a link (?region=...). Pure functions, no DOM, so
// they are unit tested in node (tests/regions.test.ts).

import type { PageSummary, Region } from "./types";

export type BoxRegion = Extract<Region, { kind: "box" }>;
export type CellRegion = Extract<Region, { kind: "cell" }>;

/** A rectangle in CSS pixels, relative to the drawn page's top left corner. */
export interface PixelRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** A rectangle in fractions (0 to 1) of the page, origin top left. */
export interface FractionRect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

const clamp01 = (n: number) => Math.min(1, Math.max(0, n));

/** A box in page fractions, placed on a page drawn at drawnWidth x drawnHeight pixels. */
export function boxToPixels(
  box: readonly [number, number, number, number],
  drawnWidth: number,
  drawnHeight: number,
): PixelRect {
  const [a, b, c, d] = box.map(clamp01) as [number, number, number, number];
  const x0 = Math.min(a, c);
  const x1 = Math.max(a, c);
  const y0 = Math.min(b, d);
  const y1 = Math.max(b, d);
  return {
    left: x0 * drawnWidth,
    top: y0 * drawnHeight,
    width: (x1 - x0) * drawnWidth,
    height: (y1 - y0) * drawnHeight,
  };
}

/** Width over height of a page, falling back to A4 portrait when the size is unknown. */
export function pageAspect(page: Pick<PageSummary, "width" | "height">): number {
  const ok = Number.isFinite(page.width) && Number.isFinite(page.height);
  if (!ok || page.width <= 0 || page.height <= 0) return 1 / Math.SQRT2;
  return page.width / page.height;
}

/**
 * The part of the page to show around a box region, in page fractions.
 * `padding` is a fraction of the page's shorter side, so the margin looks the
 * same across and down. Returns null for cells (a sheet has no drawn page).
 */
export function cropRect(region: Region, page: PageSummary, padding = 0.04): FractionRect | null {
  if (region.kind !== "box") return null;
  const aspect = pageAspect(page);
  const pad = Math.max(0, padding);
  // The shorter side is 1 in its own axis; the longer side needs a smaller fraction.
  const padX = aspect >= 1 ? pad / aspect : pad;
  const padY = aspect >= 1 ? pad : pad * aspect;
  const [a, b, c, d] = region.box.map(clamp01) as [number, number, number, number];
  return {
    x0: clamp01(Math.min(a, c) - padX),
    y0: clamp01(Math.min(b, d) - padY),
    x1: clamp01(Math.max(a, c) + padX),
    y1: clamp01(Math.max(b, d) + padY),
  };
}

/** How to draw a crop: the full page size and the shift that puts the crop in a frame. */
export interface CropView {
  pageWidth: number;
  pageHeight: number;
  offsetX: number;
  offsetY: number;
  frameWidth: number;
  frameHeight: number;
}

/**
 * Scales the page so the crop fills `frameWidth` pixels across, never beyond
 * `maxPageWidth` (a huge canvas would be slow). A zero-size crop shows the page.
 */
export function cropView(
  crop: FractionRect,
  aspect: number,
  frameWidth: number,
  maxPageWidth: number,
): CropView {
  const spanX = crop.x1 - crop.x0;
  const spanY = crop.y1 - crop.y0;
  const usable = spanX > 0 && spanY > 0;
  const x0 = usable ? crop.x0 : 0;
  const y0 = usable ? crop.y0 : 0;
  const sx = usable ? spanX : 1;
  const sy = usable ? spanY : 1;
  const pageWidth = Math.min(frameWidth / sx, Math.max(frameWidth, maxPageWidth));
  const pageHeight = pageWidth / aspect;
  return {
    pageWidth,
    pageHeight,
    offsetX: x0 * pageWidth,
    offsetY: y0 * pageHeight,
    frameWidth: sx * pageWidth,
    frameHeight: sy * pageHeight,
  };
}

/** The regions that belong on one page (or sheet). */
export function regionsOnPage(regions: readonly Region[], pageNo: number): Region[] {
  return regions.filter((region) => region.page === pageNo);
}

// --- cells ---------------------------------------------------------------------

/** 1 -> "A", 26 -> "Z", 27 -> "AA". */
export function columnName(column: number): string {
  let name = "";
  let n = Math.floor(column);
  while (n > 0) {
    const rem = (n - 1) % 26;
    name = String.fromCharCode(65 + rem) + name;
    n = Math.floor((n - 1) / 26);
  }
  return name;
}

/** "B7" for row 7, column 2. */
export function cellName(region: Pick<CellRegion, "row" | "column">): string {
  return `${columnName(region.column)}${region.row}`;
}

export const cellKey = (row: number, column: number) => `${row}:${column}`;

/** The cells to highlight on one sheet, as cellKey strings. */
export function highlightedCells(regions: readonly Region[], pageNo: number): Set<string> {
  const keys = new Set<string>();
  for (const region of regions) {
    if (region.kind === "cell" && region.page === pageNo) {
      keys.add(cellKey(region.row, region.column));
    }
  }
  return keys;
}

// --- links ---------------------------------------------------------------------

function toBase64Url(text: string): string {
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(text: string): string {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/");
  return atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
}

/** A region as base64url JSON, for ?region= in a link. */
export function encodeRegion(region: Region): string {
  const plain: Region =
    region.kind === "box"
      ? { kind: "box", page: region.page, box: [...region.box] }
      : { kind: "cell", page: region.page, row: region.row, column: region.column };
  return toBase64Url(JSON.stringify(plain));
}

const MAX_ENCODED_LENGTH = 512;
const isPositiveInt = (n: unknown): n is number => Number.isInteger(n) && (n as number) >= 1;
const isFraction = (n: unknown): n is number =>
  typeof n === "number" && Number.isFinite(n) && n >= 0 && n <= 1;

/** Reads ?region= back. Anything malformed (it comes from a URL) gives null. */
export function decodeRegion(text: string | null | undefined): Region | null {
  if (typeof text !== "string" || text.length === 0 || text.length > MAX_ENCODED_LENGTH) {
    return null;
  }
  if (!/^[A-Za-z0-9_-]+$/.test(text)) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(fromBase64Url(text));
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
  const value = parsed as Record<string, unknown>;
  if (!isPositiveInt(value.page)) return null;
  if (value.kind === "box") {
    const box = value.box;
    if (!Array.isArray(box) || box.length !== 4 || !box.every(isFraction)) return null;
    const [x0, y0, x1, y1] = box as [number, number, number, number];
    if (x0 > x1 || y0 > y1) return null;
    return { kind: "box", page: value.page, box: [x0, y0, x1, y1] };
  }
  if (value.kind === "cell") {
    if (!isPositiveInt(value.row) || !isPositiveInt(value.column)) return null;
    return { kind: "cell", page: value.page, row: value.row, column: value.column };
  }
  return null;
}

/** The document page link that source cards and review items open. */
export function pageLink(documentId: string, pageNo: number, region?: Region): string {
  const params = new URLSearchParams({ page: String(pageNo) });
  if (region) params.set("region", encodeRegion(region));
  return `/documents/${encodeURIComponent(documentId)}?${params.toString()}`;
}

/** ?page= as a page number that exists, else null. */
export function parsePageParam(text: string | null, pageNumbers: readonly number[]): number | null {
  if (text === null || !/^\d{1,6}$/.test(text)) return null;
  const n = Number(text);
  return pageNumbers.includes(n) ? n : null;
}
