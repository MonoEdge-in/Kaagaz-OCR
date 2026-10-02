// Scanned text: the words and rules behind the viewer's "Scanned text" panel and
// each value's "What was read", kept out of the components so they are unit
// tested in node (tests/viewer-scanned.test.ts). The text is shown exactly as
// read; visibleText only turns invisible control characters into markers.

import { sameRegion } from "@/components/viewer/selection";
import { routeText } from "@/components/viewer/viewer";
import type {
  ExtractedValue,
  PageReader,
  PageRoute,
  PageSummary,
  ReadLine,
  Region,
} from "@/lib/types";

// The same line as LOW_CONFIDENCE in backend/kaagaz/extraction/labels.py.
export const LOW_CONFIDENCE = 0.9;
export const CONFIDENCE_NOTE = "Confidence is the reader's own guess, not a check.";
export const FIRST_LINES = 200; // shown before "Show all N lines"

type Origin = ReadLine["origin"];

const isScore = (confidence: number | null): confidence is number =>
  typeof confidence === "number" && Number.isFinite(confidence);

/** True when the reader gave a score under LOW_CONFIDENCE. */
export function notSure(confidence: number | null): boolean {
  return isScore(confidence) && confidence < LOW_CONFIDENCE;
}

/**
 * "Reader sure (97%)", "Reader not sure (62%)", or, with no score, what that
 * means: a direct read of the file, or a reader that gives none. A score under
 * the line never rounds up to it (0.8999 says 89%, not 90%).
 */
export function confidenceText(confidence: number | null, origin: Origin): string {
  if (isScore(confidence)) {
    const percent = Math.round(confidence * 100);
    return notSure(confidence)
      ? `Reader not sure (${Math.min(percent, 89)}%)`
      : `Reader sure (${percent}%)`;
  }
  return origin === "cell" || origin === "text_layer"
    ? "Read directly from the file"
    : "No confidence given";
}

/** How a value's page was read, as a line's origin; null when it is not known. */
export function routeOrigin(route: PageRoute | null | undefined): Origin {
  if (route === "cells") return "cell";
  if (route === "text_layer" || route === "ocr") return route;
  return null;
}

/** The value at this spot, for a card or invoice item's "What was read". */
export function valueAt(values: ExtractedValue[], region: Region): ExtractedValue | undefined {
  return values.find((value) => sameRegion(value.region, region));
}

const plural = (n: number, word: string) => `${n.toLocaleString("en-IN")} ${word}${n === 1 ? "" : "s"}`;

/**
 * The heading over a reader's lines: how it was read ("Read by OCR at 200 DPI"),
 * the reader and its version (a muted line), and how many lines it was not sure
 * of (null when none).
 */
export function readerHeading(
  reader: PageReader,
  page: Pick<PageSummary, "route" | "effective_dpi">,
): { title: string; detail: string; notSureCount: string | null } {
  let title = "Second reader (PDF text layer)";
  if (reader.role === "first") {
    title = routeText(page.route) ?? "First reader";
    if (page.route === "ocr" && page.effective_dpi !== null) {
      title += ` at ${Math.round(page.effective_dpi)} DPI`;
    }
  }
  const unsure = reader.lines.filter((line) => notSure(line.confidence)).length;
  return {
    title,
    detail: `${reader.reader} ${reader.reader_version}`,
    notSureCount:
      unsure === 0
        ? null
        : `${unsure.toLocaleString("en-IN")} of ${plural(reader.lines.length, "line")}: reader not sure`,
  };
}

/** Why the second reader's text did not count on this page; null when it did. */
export function secondReaderNote(route: PageRoute | null): string | null {
  if (route === "ocr") return "Not used for this page: it was read by OCR.";
  if (route === "blank") return "Not used for this page: it is blank.";
  return null;
}

export type PanelLoad =
  | { status: "loading" }
  | { status: "error"; httpStatus: number | null }
  | { status: "ready"; reader: PageReader | undefined };

/**
 * What the panel says instead of lines: loading, an error (with Try again), not
 * read yet, a blank page, or no text found. null when there are lines to show.
 */
export function emptyState(
  load: PanelLoad,
  route: PageRoute | null,
): { text: string; retry: boolean } | null {
  if (load.status === "loading") return { text: "Loading the scanned text…", retry: false };
  if (load.status === "error") {
    return load.httpStatus === 429
      ? { text: "Too many requests, try again in a minute.", retry: true }
      : { text: "The scanned text could not be loaded. Check your connection, then try again.", retry: true };
  }
  if (route === "blank") return { text: "Blank page.", retry: false };
  if (!load.reader) return { text: "Not read yet.", retry: false };
  if (load.reader.lines.length > 0) return null;
  return route === "ocr" || route === null
    ? { text: "No text was found on this page. Upload a sharper scan.", retry: false }
    : { text: "No text was found on this page.", retry: false };
}

/** "Showing the first 2,000 lines. The rest of this page was left out."; null when all came. */
export function truncatedText(reader: Pick<PageReader, "truncated" | "lines">): string | null {
  if (!reader.truncated) return null;
  return `Showing the first ${plural(reader.lines.length, "line")}. The rest of this page was left out.`;
}

// Controls and direction marks that would hide or reorder text: C0 (but tab) and
// C1 controls, the bidi embeddings and isolates, LRM, RLM, ALM and the BOM. ZWNJ
// and ZWJ (U+200C, U+200D) are kept: Devanagari needs them.
function hidden(code: number): boolean {
  return (
    code <= 0x1f ||
    (code >= 0x7f && code <= 0x9f) ||
    code === 0x061c ||
    code === 0x200e ||
    code === 0x200f ||
    (code >= 0x202a && code <= 0x202e) ||
    (code >= 0x2066 && code <= 0x2069) ||
    code === 0xfeff
  );
}

/** The text as read, with a tab as a space and every hidden control shown as ⟦U+202E⟧. */
export function visibleText(text: string): string {
  let shown = "";
  for (const char of text) {
    const code = char.codePointAt(0) ?? 0;
    if (code === 0x09) shown += " ";
    else if (hidden(code)) shown += `⟦U+${code.toString(16).toUpperCase().padStart(4, "0")}⟧`;
    else shown += char;
  }
  return shown;
}

/**
 * True when the line is inside the cited spot: its box's centre is in the
 * region's box (a citation's box covers every line it quotes), or it is the same cell.
 */
export function lineMarked(line: Pick<ReadLine, "region">, region: Region | null): boolean {
  if (region === null || line.region.page !== region.page) return false;
  if (line.region.kind === "cell" || region.kind === "cell") return sameRegion(line.region, region);
  const [x0, y0, x1, y1] = line.region.box;
  const [rx0, ry0, rx1, ry1] = region.box;
  const x = (x0 + x1) / 2;
  const y = (y0 + y1) / 2;
  const e = 1e-6;
  return x >= rx0 - e && x <= rx1 + e && y >= ry0 - e && y <= ry1 + e;
}
