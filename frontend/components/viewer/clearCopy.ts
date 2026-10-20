// Read clearly (Tushar, 19 Oct 2026): the words and rules of the clean copy, kept pure so
// they are unit tested in node (tests/read-clearly.test.ts). The lines are the main
// reader's, as read and in order; nothing is added or changed, only grouped: headings,
// "Label: value" lines, lists, table cells back in a grid by row and column, and paragraphs.

import type { ReadLine, ReadingMark, TranslateLanguage } from "@/lib/types";
import { lineMarked, notSure, visibleText } from "./scanned";

export const NOT_SURE_TIP = "Not read for sure — check the original";
export const TRANSLATION_LABEL = "Translation — the original text is above";
export const TRANSLATING = "Translating…";
export const BUSY = "The translation service is busy right now. The exact text is still here; try again in a minute.";
export const LEFT_OUT = "This document is long, so the clean copy stops here. The rest is on the page images.";
export const MAX_TRANSLATE_PAGES = 20; // "Translate all pages" asks for at most this many
export const TRANSLATE_AT_ONCE = 3; // requests in flight at a time

export const LANGUAGES: { id: TranslateLanguage | null; name: string; lang?: string }[] = [
  { id: null, name: "Original" },
  { id: "en", name: "English", lang: "en" },
  { id: "hi", name: "हिंदी", lang: "hi" },
  { id: "mr", name: "मराठी", lang: "mr" },
];

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

/** The whole copy as plain text, for Copy all: each page under its "Page 1 of 4". */
export function copyText(pages: readonly { page_no: number; lines: readonly ReadLine[] }[]): string {
  return pages
    .map((page) => [pageOf(page.page_no, pages.length), ...page.lines.map((line) => visibleText(line.text))].join("\n"))
    .join("\n\n");
}
