// The words and rules of the Scanned text panel, unit tested in node
// (tests/viewer-scanned.test.ts). Text is shown as the server sends it (PRD 9 F-12: with its fixes).

import { sameRegion } from "@/components/viewer/selection";
import { BACKUP_ISSUES } from "@/components/viewer/viewer";
import type {
  ExtractedValue,
  PageReader,
  PageRoute,
  PageSummary,
  ReadLine,
  Region,
  WordFlag,
} from "@/lib/types";

// The same line as LOW_CONFIDENCE in backend/kaagaz/extraction/labels.py.
export const LOW_CONFIDENCE = 0.9;
// Shown once wherever a "Reader not sure" tag can appear (UI quality council, 15 Oct 2026).
export const CONFIDENCE_NOTE = "'Not sure' is the reader's own view, not a check.";
export const FIRST_LINES = 200; // shown before "Show all N lines"
// PRD 5 U8: "First reader" and "Second reader" in words people know.
export const MAIN_READER = "Main reader";
export const CHECKER = "Checker (text inside the PDF)";

type Origin = ReadLine["origin"];

const isScore = (confidence: number | null): confidence is number =>
  typeof confidence === "number" && Number.isFinite(confidence);

/** True when the reader gave a score under LOW_CONFIDENCE. */
export function notSure(confidence: number | null): boolean {
  return isScore(confidence) && confidence < LOW_CONFIDENCE;
}

/**
 * "Reader not sure" under LOW_CONFIDENCE, never a number; nothing for a line it was sure of.
 * With no score, "Read directly from the file" for a cell or the text layer, else null.
 */
export function confidenceText(confidence: number | null, origin: Origin): string | null {
  if (isScore(confidence)) return notSure(confidence) ? "Reader not sure" : null;
  return origin === "cell" || origin === "text_layer" ? "Read directly from the file" : null;
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

export const plural = (n: number, word: string) => `${n.toLocaleString("en-IN")} ${word}${n === 1 ? "" : "s"}`;

/**
 * "Main reader" (or "Backup reader" for a page the main reader handed on) and "2 of 3 lines:
 * reader not sure" (or null). The words name no engine, model or scan setting (PRD 5 U8; UI
 * quality council, 15 Oct 2026): the main reader, and the checker (the text inside the PDF).
 */
export function readerHeading(
  reader: PageReader,
  page: Pick<PageSummary, "read_issues">,
): { title: string; notSureCount: string | null } {
  let title = CHECKER;
  if (reader.role === "first") {
    title = page.read_issues.some((issue) => BACKUP_ISSUES.includes(issue)) ? "Backup reader" : MAIN_READER;
  }
  const unsure = reader.lines.filter((line) => notSure(line.confidence)).length;
  return {
    title,
    notSureCount:
      unsure === 0
        ? null
        : `${unsure.toLocaleString("en-IN")} of ${plural(reader.lines.length, "line")}: reader not sure`,
  };
}

/** Why the checker's text did not count on this page; null when it did. */
export function secondReaderNote(route: PageRoute | null): string | null {
  if (route === "ocr") return "This page is a scan, so it carries no text of its own to compare.";
  if (route === "blank") return "Not used for this page: it is blank.";
  return null;
}

export type PanelLoad =
  | { status: "loading" }
  | { status: "error"; httpStatus: number | null }
  | { status: "ready"; reader: PageReader | undefined };

/** What the panel says instead of lines; null when there are lines to show. */
export function emptyState(
  load: PanelLoad,
  route: PageRoute | null,
): { text: string; retry: boolean } | null {
  if (load.status === "loading") return { text: "Loading the scanned text…", retry: false };
  if (load.status === "error") {
    if (load.httpStatus === 429) return { text: "Too many requests, try again in a minute.", retry: true };
    return load.httpStatus === null
      ? { text: "The scanned text could not be loaded. Check your connection, then try again.", retry: true }
      : { text: "The scanned text could not be loaded. Try again.", retry: true };
  }
  if (route === "blank") return { text: "Blank page.", retry: false };
  if (!load.reader) return { text: "Not read yet.", retry: false };
  if (load.reader.lines.length > 0) return null;
  // A sharper scan helps OCR only, never the checker (the PDF text layer).
  return load.reader.role === "first" && (route === "ocr" || route === null)
    ? { text: "No text was found on this page. Upload a sharper scan.", retry: false }
    : { text: "No text was found on this page.", retry: false };
}

/** "Showing the first 2,000 lines. The rest of this page was left out."; null when all came. */
export function truncatedText(reader: Pick<PageReader, "truncated" | "lines">): string | null {
  if (!reader.truncated) return null;
  return `Showing the first ${plural(reader.lines.length, "line")}. The rest of this page was left out.`;
}

// C0 and C1 controls, bidi embeddings and isolates, LRM, RLM, ALM and the BOM:
// they hide or reorder text. ZWNJ and ZWJ are kept: Devanagari needs them.
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

/** The text as read: line breaks kept (CR LF and CR as LF), a tab as a space, any other hidden one as ⟦U+202E⟧. */
export function visibleText(text: string): string {
  let shown = "";
  for (const char of text.replace(/\r\n?/g, "\n")) {
    const code = char.codePointAt(0) ?? 0;
    if (code === 0x0a) shown += char;
    else if (code === 0x09) shown += " ";
    else if (hidden(code)) shown += `⟦U+${code.toString(16).toUpperCase().padStart(4, "0")}⟧`;
    else shown += char;
  }
  return shown;
}

/** True when the line's box centre is in the cited box (which covers every quoted line), or it is the same cell. */
/** The line that holds a spot pressed on the page (24 Oct 2026: press a box, see its text):
 * on its page, the line its box overlaps most, a cell only itself; -1 for none. O(n). */
export function lineHolding(lines: readonly Pick<ReadLine, "region">[], region: Region | null): number {
  if (region === null) return -1;
  let best = -1;
  let most = 0;
  lines.forEach((line, index) => {
    if (line.region.page !== region.page) return;
    if (line.region.kind === "cell" || region.kind === "cell") {
      if (sameRegion(line.region, region)) best = index;
      return;
    }
    const [x0, y0, x1, y1] = line.region.box;
    const [rx0, ry0, rx1, ry1] = region.box;
    const area = Math.max(0, Math.min(x1, rx1) - Math.max(x0, rx0)) * Math.max(0, Math.min(y1, ry1) - Math.max(y0, ry0));
    if (area > most) {
      most = area;
      best = index;
    }
  });
  return best;
}

export function lineMarked(line: Pick<ReadLine, "region">, region: Region | null): boolean {
  if (region === null || line.region.page !== region.page) return false;
  if (line.region.kind === "cell" || region.kind === "cell") return sameRegion(line.region, region);
  const [x0, y0, x1, y1] = line.region.box;
  const [rx0, ry0, rx1, ry1] = region.box;
  const e = 1e-6;
  // #523: a value cited on one written line's band of a block lies inside the block's box.
  return centreIn(line.region, region) || (rx0 >= x0 - e && rx1 <= x1 + e && ry0 >= y0 - e && ry1 <= y1 + e);
}

/** True when box `inner`'s centre is in box `outer` on the same page, or it is the same cell. */
function centreIn(inner: Region, outer: Region): boolean {
  if (inner.page !== outer.page) return false;
  if (inner.kind === "cell" || outer.kind === "cell") return sameRegion(inner, outer);
  const [x0, y0, x1, y1] = inner.box;
  const [rx0, ry0, rx1, ry1] = outer.box;
  const x = (x0 + x1) / 2;
  const y = (y0 + y1) / 2;
  const e = 1e-6;
  return x >= rx0 - e && x <= rx1 + e && y >= ry0 - e && y <= ry1 + e;
}

// PRD 9 F-13, F-14: text compared with its whitespace squashed (a flag's text as read is; a raw line's is not).
export const squash = (text: string) => text.replace(/\s+/g, " ").trim();
const area = (region: Region) =>
  region.kind === "box" ? (region.box[2] - region.box[0]) * (region.box[3] - region.box[1]) : 0;

/** #614: a flag is on a row when it is the row's own line flag (the same spot and the same text
 * as read: a reader gives every line of a block the block's box, or the page's), or a word or
 * value whose centre is in the row. Never lineMarked's #523 test: it hung a table-sized line
 * flag on every row inside it, so Fix text on one row saved over another line.
 * PRD 9 F-12: or the row's key (its spot and text as read), once a fix changed the text it shows. */
function onRow(
  flag: Pick<WordFlag, "region" | "as_read"> & { kind?: WordFlag["kind"]; word_key?: string },
  row: Pick<ReadLine, "region" | "text" | "key">,
): boolean {
  if (flag.kind === "line") {
    const own = squash(flag.as_read) === squash(row.text) || (row.key !== undefined && flag.word_key === row.key);
    return sameRegion(flag.region, row.region) && own;
  }
  return centreIn(flag.region, row.region);
}

/** #614: the key of a row's open fix box, the row and its flag, so one box opens at a time. */
export const fixBoxKey = (index: number, flag: Pick<WordFlag, "word_key">) => `${index}:${flag.word_key}`;

// The reader's mark for a part of a line it could not read (page_quality.ILLEGIBLE_MARK).
const UNREAD = "[?]";
// Vrushit UAT 2 (27 Oct 2026, #417): said over the lines while a reviewer can fix a word in them.
// #443: or confirm it as read.
export const FIX_NOTE =
  "To type the right word in place of [?] or a marked word, or confirm it as read, press its line or Fix text.";
// Said there instead to a member, who cannot fix a word (#443: any marked word, not only a [?]).
export const FIX_MEMBER_NOTE = "Only a reviewer or an admin can fix or confirm a marked word.";

/** The fix box under a line (#417): what it asks, and the text it starts with (nothing for an
 * unread [?]; else the word as it reads now, to correct). #443: a mark that is no word (a lone
 * "." or other punctuation) is said to be one, so Confirm reads as "leave it as it is". */
export function fixBox(flag: Pick<WordFlag, "text">): { hint: string; draft: string } {
  if (flag.text === UNREAD) return { hint: "Type the word you see on the page in place of [?].", draft: "" };
  const stray = /^[\p{P}\p{S}\s]+$/u.test(flag.text);
  return {
    hint: stray
      ? `'${flag.text}' may be a stray mark. Save it as it is to leave it as read, or type what the page shows.`
      : `Type the word you see on the page in place of '${flag.text}'.`,
    draft: flag.text,
  };
}

/**
 * The flagged word a line's Fix text works on (#431): a word mark ([?] or an unsure word) before
 * the line's own flag (a line whose spot is not confirmed also holds its marks, and comes first),
 * so a Save replaces that word, never the whole line; the line's own flag once its words are done.
 * #614: a word or value is only the smallest of the page's `lines` holding its centre, never a
 * big line's around that line. O(flags + the line's words x lines).
 */
export function wordToFix<T extends Pick<WordFlag, "kind" | "region" | "as_read"> & { word_key?: string }>(
  flags: readonly T[],
  line: Pick<ReadLine, "region" | "text" | "key">,
  lines: readonly Pick<ReadLine, "region">[],
): T | undefined {
  const size = area(line.region);
  const inSmaller = (each: T) => lines.some((other) => area(other.region) < size && centreIn(each.region, other.region));
  const inLine = flags.filter((each) => onRow(each, line) && (each.kind === "line" || !inSmaller(each)));
  return inLine.find((each) => each.kind === "mark") ?? inLine[0];
}

/** What a Save in the fix box settles (#431): the word, and its line's own flag when the line is
 * only that word (a signature read as [?]: one word on the page, flagged twice). #614: only the
 * flag of `row`, the line the Save came from, never another line around it or in its box. */
export function fixesFor<T extends Pick<WordFlag, "kind" | "region" | "text" | "as_read"> & { word_key?: string }>(
  flag: T,
  flags: readonly T[],
  row: Pick<ReadLine, "region" | "text" | "key">,
): T[] {
  const lineIsWord = (each: T) => each !== flag && each.kind === "line" && each.text === flag.text && onRow(each, row);
  return [flag, ...flags.filter(lineIsWord)];
}

/** PRD 9 F-14: the flag a row shows in red: at its spot, with its key or its text (squashed), so a
 * line in a block (a Mistral block's lines share its box) never shows another line's flag. */
export function rowFlag<T extends Pick<WordFlag, "region" | "as_read" | "word_key">>(
  flags: readonly T[],
  row: Pick<ReadLine, "region" | "text" | "key">,
): T | undefined {
  const own = (each: T) => each.word_key === row.key || squash(each.as_read) === squash(row.text);
  return flags.find((each) => sameRegion(each.region, row.region) && own(each));
}

