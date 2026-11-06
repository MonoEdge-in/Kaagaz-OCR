// Read clearly (Tushar, 19 Oct 2026): the words and rules of the clean copy, kept pure so
// they are unit tested in node (tests/read-clearly.test.ts). The lines are the main
// reader's, as read and in order; nothing is added or changed, only grouped: headings,
// "Label: value" lines, lists, table cells back in a grid by row and column, and paragraphs.

import { failureText } from "@/components/review/wordReview";
import { ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { ClearCopy, ExtractedValue, LineCorrection, ReadLine, ReadingMark } from "@/lib/types";
import { fieldText } from "./labels";
import { lineMarked, notSure, visibleText } from "./scanned";

/** The clean copy's one name: its tab, its heading and its button (document panel UX, 25 Oct 2026; was "Read clearly"). */
export const CLEAN_COPY = "Clean copy";
export const NOT_SURE_TIP = "Not read for sure — check the original";
export const LEFT_OUT = "This document is long, so the clean copy stops here. The rest is on the page images.";

export type Block =
  | { type: "heading"; line: ReadLine }
  | { type: "paragraph"; line: ReadLine }
  | { type: "pair"; line: ReadLine; label: number } // label: the characters up to and with ":"
  | { type: "list"; items: ReadLine[] }
  | { type: "table"; rows: ReadLine[][][] }; // rows of cells; a cell holds its lines in order

const HEADINGS = new Set(["title", "heading"]);
// "Name: Sita", "नाव : सीता": a short label, a colon, a space, then the value.
const PAIR = /^([^:\n]{1,40}):[ \t]+\S/;

/** The lines grouped into blocks, in the reader's order. O(n) plus the tables' grids. */
export function blocks(lines: readonly ReadLine[]): Block[] {
  const out: Block[] = [];
  const tables = new Map<string, { cells: ReadLine[]; at: number }>();
  for (const line of lines) {
    const table = line.table;
    if (line.kind === "table_cell" && table) {
      const found = tables.get(table.id);
      if (found) found.cells.push(line);
      else {
        tables.set(table.id, { cells: [line], at: out.length });
        out.push({ type: "table", rows: [] });
      }
      continue;
    }
    const last = out.at(-1);
    if (line.kind === "list_item") {
      if (last?.type === "list") last.items.push(line);
      else out.push({ type: "list", items: [line] });
    } else if (HEADINGS.has(line.kind ?? "")) {
      out.push({ type: "heading", line });
    } else {
      const pair = PAIR.exec(line.text);
      out.push(pair ? { type: "pair", line, label: (pair[1]?.length ?? 0) + 1 } : { type: "paragraph", line });
    }
  }
  for (const { cells, at } of tables.values()) out[at] = { type: "table", rows: grid(cells) };
  return out;
}

/** The cells by row and column; a row or column no cell is in is left out. */
function grid(cells: readonly ReadLine[]): ReadLine[][][] {
  const rows = [...new Set(cells.map((cell) => cell.table?.row ?? 0))].sort((a, b) => a - b);
  const cols = [...new Set(cells.map((cell) => cell.table?.col ?? 0))].sort((a, b) => a - b);
  const out = rows.map(() => cols.map((): ReadLine[] => []));
  for (const cell of cells) {
    out[rows.indexOf(cell.table?.row ?? 0)]?.[cols.indexOf(cell.table?.col ?? 0)]?.push(cell);
  }
  return out;
}

export interface Part {
  text: string; // shown through visibleText
  unsure: boolean;
  label?: boolean; // the "Label:" of a pair, shown in bold
}

/** The parts before (`head`) or from the shown offset `cut`; a head part is a label. */
export function splitAt(shown: readonly Part[], cut: number, head: boolean): Part[] {
  const out: Part[] = [];
  let at = 0;
  for (const part of shown) {
    const from = Math.max(0, Math.min(part.text.length, cut - at));
    const text = head ? part.text.slice(0, from) : part.text.slice(from);
    if (text) out.push({ ...part, text, label: head });
    at += part.text.length;
  }
  return out;
}

/** A pair's parts, its label (up to and with ":") marked as one. */
export function pairParts(line: ReadLine, marks: readonly ReadingMark[], label: number): Part[] {
  const shown = parts(line, marks);
  const cut = visibleText(line.text.slice(0, label)).length;
  return [...splitAt(shown, cut, true), ...splitAt(shown, cut, false)];
}

/**
 * The line in parts, the words not read for sure marked: each unsure or [?] mark inside
 * the line's box whose word is in its text, every "[?]", or the whole line when its
 * reader was not sure of it. O(c x m) for c characters and m marks.
 */
export function parts(line: ReadLine, marks: readonly ReadingMark[]): Part[] {
  const text = line.text;
  // #527: a person set a corrected line's words, so none of it is "not read for sure".
  if (line.correction) return [{ text: visibleText(text), unsure: false }];
  if (notSure(line.confidence)) return [{ text: visibleText(text), unsure: true }];
  const spans: [number, number][] = [];
  let cursor = 0;
  for (const mark of marks) {
    if (mark.quality === "unpinned" || !mark.text || !lineMarked(mark, line.region)) continue;
    const start = text.indexOf(mark.text, cursor);
    if (start < 0) continue;
    spans.push([start, start + mark.text.length]);
    cursor = start + mark.text.length;
  }
  for (let at = text.indexOf("[?]"); at >= 0; at = text.indexOf("[?]", at + 3)) {
    if (!spans.some(([s, e]) => at < e && at + 3 > s)) spans.push([at, at + 3]);
  }
  spans.sort((a, b) => a[0] - b[0]);
  const out: Part[] = [];
  let from = 0;
  for (const [start, end] of spans) {
    if (start < from) continue;
    if (start > from) out.push({ text: visibleText(text.slice(from, start)), unsure: false });
    out.push({ text: visibleText(text.slice(start, end)), unsure: true });
    from = end;
  }
  if (from < text.length || out.length === 0) out.push({ text: visibleText(text.slice(from)), unsure: false });
  return out;
}

/** "Page 1 of 4". */
export const pageOf = (pageNo: number, total: number) => `Page ${pageNo} of ${total}`;

// The clean copy's Text tab words (document panel UX, 25 Oct 2026).
/** The one heading over a page: "Page 1" when there is one, else "Page 2 of 4". */
export const pageTitle = (pageNo: number, total: number) => (total === 1 ? `Page ${pageNo}` : pageOf(pageNo, total));
export const OPENED_NOTE = "The line you opened";
export const WIDE_NOTE = "Scroll sideways to see all columns";
export const COPIED = "Copied";
export const COPY_BLOCKED = "Copying is blocked here. Select the text and copy it instead.";

/** The whole copy as plain text, for Copy all: each page under its "Page 1 of 4". */
export function copyText(pages: readonly { page_no: number; lines: readonly ReadLine[] }[]): string {
  return pages
    .map((page) => [pageOf(page.page_no, pages.length), ...page.lines.map((line) => visibleText(line.text))].join("\n"))
    .join("\n\n");
}

/**
 * Export as Data (Vrushit UAT, 25 Oct 2026): the document's raw data as plain text, the text
 * as read page by page (copyText, with the long-document note when it was cut), then the
 * values found, "Invoice date: 19/03/22 (page 1)".
 */
export function dataText(copy: ClearCopy, values: readonly Pick<ExtractedValue, "field" | "value" | "region">[]): string {
  const found = values.map((value) => `${fieldText(value.field)}: ${visibleText(value.value)} (page ${value.region.page})`);
  return [
    copyText(copy.pages),
    ...(copy.truncated ? [LEFT_OUT] : []),
    ...(found.length > 0 ? [["Values found", ...found].join("\n")] : []),
  ].join("\n\n");
}

// --- Correct a line (#527, Tushar, 7 Nov 2026): a reviewer or an admin edits a line ---

export const MAX_LINE = 5000; // the API's MAX_TEXT (routers/word_review.py)
export const LINE_SAVED = "Line corrected. Search and answers use the new text.";
export const LINE_SAVED_HERE = "Line corrected here, but search could not be updated for it."; // no piece took it
// The server's rule for a typed line (routers/word_review.py, names.one_line and clean_name):
// each run of line breaks or tabs a single space, control and hidden characters removed (ZWNJ,
// ZWJ and a flag emoji's tags kept), trimmed.
const BREAKS = /(?:\r\n|[\n\r\u2028\u2029\t])+/g;
const FLAG = /(\u{1F3F4}[\u{E0020}-\u{E007E}]+\u{E007F})/u;
const HIDDEN = /[\x00-\x1f\x7f-\x9f]|(?![\u200c\u200d])[\p{Cf}\p{Zl}\p{Zp}]/gu;

/** A line's text as one typed line: what its text box starts with, so nothing glued or invisible is saved. */
export const oneLine = (text: string) =>
  text
    .replace(BREAKS, " ")
    .split(FLAG)
    .map((part, index) => (index % 2 ? part : part.replace(HIDDEN, "")))
    .join("")
    .trim();
export const LINE_UNDONE = "Undone. The line reads as before.";
export const LINE_CHANGED = "This line changed since you opened it. Reload and try again.";
export const STILL_READING = "The document is still being read.";

/** "Corrected by Ravi Sharma, 7 Nov 2026". */
export const correctedText = (correction: LineCorrection) => `Corrected by ${correction.by}, ${formatDate(correction.at)}`;

/** "Read as: Total 1180": the text the correction replaced, hidden characters shown. */
export const readAsText = (correction: LineCorrection) => `Read as: ${visibleText(correction.before_text)}`;

/** A refused save or Undo in plain words; any other refusal as the word edit says it. */
export function lineFailure(caught: unknown): string {
  if (caught instanceof ApiError && caught.code === "changed_since") return LINE_CHANGED;
  if (caught instanceof ApiError && caught.code === "not_ready") return STILL_READING;
  return failureText(caught);
}

/** The copy with `saved` in place of the line with its key; nothing else changes. */
export const replaceLine = (copy: ClearCopy, saved: ReadLine): ClearCopy => ({
  ...copy,
  pages: copy.pages.map((page) => ({
    ...page,
    lines: page.lines.map((line) => (line.key === saved.key ? { ...line, ...saved } : line)),
  })),
});
