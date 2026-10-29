// My words in the viewer, the logic (council B2, 3 Oct 2026): "Add meaning" on a value spot
// or a scanned-text line, and this user's own spots on the page as outlined,
// numbered markers with a list. Pure logic, unit tested in node
// (tests/my-words-viewer.test.ts). Words are the user's text and the line is
// the document's: shown as text only, never logged.

import type { PixelRect } from "@/lib/regions";
import { boxToPixels, cellKey } from "@/lib/regions";
import type { MySpot, Region } from "@/lib/types";
import { addBody, fold, textProblem, tidy } from "./words";
import type { NewWordBody, WordDraft } from "./words";

export const CELL_MAX = 10_000; // the server's row and column limit
const MAX_PICKS = 12; // words of a line offered to pick from
const MIN_MARKER_PX = 12; // a marker is never drawn smaller than this, across or down

export type SpotBody = { document_id: string; page_no: number; region: Region };
export type ViewerWordBody = NewWordBody & { spot: SpotBody };

const isCell = (n: unknown): n is number => Number.isInteger(n) && (n as number) >= 1 && (n as number) <= CELL_MAX;
const clamp01 = (n: number) => Math.min(1, Math.max(0, n));

/**
 * The region as the server takes it for a spot, or null when it would refuse it:
 * a box is kept inside the page (a reader's box can poke out by rounding) with
 * its corners in order and some size; a cell's row and column are 1 to 10,000.
 */
export function spotRegion(region: unknown): Region | null {
  const value = region as Partial<Record<string, unknown>> | null;
  if (typeof value !== "object" || value === null || !Number.isInteger(value.page) || (value.page as number) < 1) {
    return null;
  }
  const page = value.page as number;
  if (value.kind === "cell") {
    return isCell(value.row) && isCell(value.column) ? { kind: "cell", page, row: value.row, column: value.column } : null;
  }
  const box = value.box;
  if (value.kind !== "box" || !Array.isArray(box) || box.length !== 4) return null;
  if (!box.every((n) => typeof n === "number" && Number.isFinite(n))) return null;
  const [a, b, c, d] = (box as number[]).map(clamp01) as [number, number, number, number];
  const x0 = Math.min(a, c);
  const y0 = Math.min(b, d);
  const x1 = Math.max(a, c);
  const y1 = Math.max(b, d);
  return x0 < x1 && y0 < y1 ? { kind: "box", page, box: [x0, y0, x1, y1] } : null;
}

/** POST /me/words for a word on this spot; null when the spot can't be saved. */
export function viewerBody(draft: WordDraft, userId: string, documentId: string, region: Region): ViewerWordBody | null {
  const checked = spotRegion(region);
  if (!checked) return null;
  return { ...addBody(draft, userId), spot: { document_id: documentId, page_no: checked.page, region: checked } };
}

/** The words of a line or value, as read, that can be a word: punctuation at the ends dropped, each once. */
export function pickableWords(text: string): string[] {
  const seen = new Set<string>();
  const words: string[] = [];
  for (const part of tidy(text).split(" ")) {
    const word = part.replace(/^[^\p{L}\p{M}\p{Nd}]+|[^\p{L}\p{M}\p{Nd}]+$/gu, "");
    if (!word || textProblem(word, "word") || seen.has(fold(word))) continue;
    seen.add(fold(word));
    words.push(word);
    if (words.length === MAX_PICKS) break;
  }
  return words;
}

/** The form's word, as read: the whole text when it can be a word, else its first word, else empty. */
export function wordFromText(text: string): string {
  const whole = tidy(text);
  if (!textProblem(whole, "word")) return whole;
  return pickableWords(text)[0] ?? "";
}

/** What the list says when this page's words could not be loaded (by HTTP status; null: no answer). */
export function spotsLoadText(httpStatus: number | null): string {
  if (httpStatus === 429) return "Too many requests. Your words on this page will show in a minute.";
  if (httpStatus === 404) return "This document isn't there any more.";
  return httpStatus === null
    ? "Your words on this page could not be loaded. Check your internet connection, then try again."
    : "Your words on this page could not be loaded. Try again.";
}

export interface Marker {
  id: string;
  n: number; // shown on the marker and its list item, so it is not colour alone
  word: string;
  meaning: string;
  region: Region;
}

/** The page's spots as numbered markers, oldest first; a spot for another page or with a bad region is left out. */
export function markersOf(spots: readonly MySpot[], pageNo: number): Marker[] {
  const markers: Marker[] = [];
  for (const spot of spots) {
    const region = spotRegion(spot.region);
    if (!region || region.page !== pageNo || typeof spot.word !== "string" || typeof spot.meaning !== "string") continue;
    markers.push({ id: spot.id, n: markers.length + 1, word: spot.word, meaning: spot.meaning, region });
  }
  return markers;
}

/** "'malak' means 'owner'": a marker's words for its list item and screen readers. */
export const markerText = (marker: Pick<Marker, "word" | "meaning">) => `'${marker.word}' means '${marker.meaning}'`;

type Box = readonly [number, number, number, number];

/**
 * A stored box in the page's frame before its file's own turn (/Rotate, 0, 90, 180 or 270
 * degrees clockwise). Readers store fractions of the whole page as shown, that turn applied
 * (backend pdf_reader.py), white margins and all, so a scan smaller than its page needs no
 * offset. The PDF viewer draws marks before the turn and turns them with the page (#442).
 */
export function unturnedBox(box: Box, turn: number): Box {
  const [x0, y0, x1, y1] = box;
  switch (turn) {
    case 90:
      return [y0, 1 - x1, y1, 1 - x0];
    case 180:
      return [1 - x1, 1 - y1, 1 - x0, 1 - y0];
    case 270:
      return [1 - y1, x0, 1 - y0, x1];
    default:
      return box;
  }
}

/** Where a box marker goes on a page drawn at width x height: never under MIN_MARKER_PX, kept on the page. */
export function markerRect(box: Box, width: number, height: number): PixelRect {
  const px = boxToPixels(box, width, height);
  const grow = (start: number, size: number, limit: number) => {
    const wanted = Math.min(Math.max(size, MIN_MARKER_PX), limit);
    const from = Math.min(Math.max(0, start - (wanted - size) / 2), Math.max(0, limit - wanted));
    return [from, wanted] as const;
  };
  const [left, w] = grow(px.left, px.width, width);
  const [top, h] = grow(px.top, px.height, height);
  return { left, top, width: w, height: h };
}

/** The markers in each cell of a sheet, by "row:column" (lib/regions cellKey). */
export function markedCells(markers: readonly Marker[]): Map<string, Marker[]> {
  const cells = new Map<string, Marker[]>();
  for (const marker of markers) {
    if (marker.region.kind !== "cell") continue;
    const key = cellKey(marker.region.row, marker.region.column);
    cells.set(key, [...(cells.get(key) ?? []), marker]);
  }
  return cells;
}

// --- "Show my words": remembered in this browser; the page works without storage ---

export const SHOW_KEY = "kaagaz.viewer.show-my-words";
type Store = Pick<Storage, "getItem" | "setItem">;

/** localStorage, or null where reading it throws (blocked site data, some private windows). */
export function browserStore(): Store | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

/** On unless it was turned off here; on when the store can't be read. */
export function readShowMyWords(store: Store | null): boolean {
  try {
    return store?.getItem(SHOW_KEY) !== "0";
  } catch {
    return true;
  }
}

/** Remember the choice; a store that throws only means it is not remembered. */
export function writeShowMyWords(store: Store | null, on: boolean): void {
  try {
    store?.setItem(SHOW_KEY, on ? "1" : "0");
  } catch {
    // not remembered; the toggle still works for this page
  }
}
