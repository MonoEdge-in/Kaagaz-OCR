// #614 (live since the 12 Nov release): Fix text on one line could save another line's text over
// a line that was right. The #523 review taught lineMarked that a cited band inside a block's box
// marks the block; wordToFix, fixesFor and withFixes call it with the flag in the line's place, so
// a table-sized line flag (a model box, a blob line) sat on every row inside it. Each of those rows
// offered it, the same box opened under all of them, and a Save wrote that row's text over it.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { fixBoxKey, fixesFor, lineMarked, withFixes, wordToFix } from "@/components/viewer/scanned";
import type { WordFlag } from "@/lib/types";

const panel = readFileSync(join(__dirname, "..", "components/viewer/ScannedTextPanel.tsx"), "utf8");

type Box = [number, number, number, number];
const region = (box: Box) => ({ kind: "box" as const, page: 1, box });
const flag = (kind: WordFlag["kind"], as_read: string, box: Box, changes: Partial<WordFlag> = {}): WordFlag => ({
  word_key: `${kind}-${as_read}-${box.join(",")}`,
  kind,
  page_no: 1,
  region: region(box),
  as_read,
  text: as_read,
  band: "review",
  reason: "spot_unconfirmed",
  reason_text: "spot on the page not confirmed",
  score: null,
  alternatives: [],
  value_id: null,
  state: "open",
  ensemble: null,
  change: null,
  ...changes,
});

// A table read as one line whose box no word confirmed, and two rows of the table inside it.
const BIG: Box = [0.05, 0.3, 0.95, 0.7];
const big = flag("line", "Item Qty Rate Amount Rice 2 40 80 Wheat 1 30 30", BIG);
const ROW: Box = [0.05, 0.4, 0.95, 0.45];
const inner = { text: "[?] 2 40 80", region: region(ROW) };
const middle = { text: "Wheat 1 30 30", region: region([0.05, 0.48, 0.95, 0.52]) }; // through big's centre
const bigRow = { text: big.as_read, region: big.region };
const lines = [bigRow, inner, middle];
const mark = flag("mark", "[?]", [0.05, 0.4, 0.1, 0.45], { reason: "low_confidence" });

describe("#614 a row's Fix text works on that row's own flags only", () => {
  it("never offers a big line's flag on the rows inside it, only on its own row", () => {
    expect(wordToFix([big], inner, lines)).toBeUndefined();
    expect(wordToFix([big], middle, lines)).toBeUndefined(); // even the row through its centre
    expect(wordToFix([big], bigRow, lines)).toBe(big);
    expect(wordToFix([big, mark], inner, lines)).toBe(mark); // the row's own word still
  });

  it("saves the word alone, and never one line over another line inside or around it", () => {
    expect(fixesFor(mark, [big, mark], inner)).toEqual([mark]);
    // A blob read as [?] with a row inside it also read as [?]: two lines, each saved alone.
    const blob = flag("line", "[?]", BIG);
    const row = flag("line", "[?]", ROW);
    expect(fixesFor(blob, [blob, row], { text: "[?]", region: blob.region })).toEqual([blob]);
    expect(fixesFor(row, [blob, row], { text: "[?]", region: row.region })).toEqual([row]);
    // #431 still: a signature read as [?], one spot flagged twice, settled by one Save.
    const SIGN: Box = [0.075, 0.25, 0.2, 0.28];
    const signLine = flag("line", "[?]", SIGN);
    const signMark = flag("mark", "[?]", SIGN);
    expect(fixesFor(signMark, [signLine, signMark], { text: "[?]", region: region(SIGN) })).toEqual([signMark, signLine]);
  });

  // #614 review: a reader gives every line of a block the block's box, or the whole page's
  // (mistral_reader.page_read; pipeline "Two lines with one box"), so a spot alone names no line.
  it("tells apart lines that share one box by their text as read", () => {
    const rice = flag("line", "Rice  2 40 80", BIG); // spaces are not text
    const wheat = flag("line", "Wheat 1 30 30", BIG);
    const riceRow = { text: "Rice 2 40 80", region: region(BIG) };
    const wheatRow = { text: "Wheat 1 30 30\n", region: region(BIG) };
    const rows = [riceRow, wheatRow];
    expect(wordToFix([rice, wheat], riceRow, rows)).toBe(rice);
    expect(wordToFix([rice, wheat], wheatRow, rows)).toBe(wheat);
    expect(wordToFix([wheat], riceRow, rows)).toBeUndefined();
    // A row read as [?] alone in that box is its own line: a [?] saved on another row skips it.
    const lone = flag("line", "[?]", BIG);
    expect(fixesFor(mark, [rice, lone, mark], riceRow)).toEqual([mark]);
    expect(fixesFor(mark, [rice, lone, mark], { text: "[?]", region: region(BIG) })).toEqual([mark, lone]);
    // A row read as "40" alone in the same box, typed as "45": shown in that row only.
    const forty = { ...flag("line", "40", BIG), text: "45" };
    expect(withFixes(riceRow, [forty])).toBe("Rice 2 40 80");
    expect(withFixes({ text: "40", region: region(BIG) }, [forty])).toBe("45");
  });

  // #614 review: a [?] inside a big line read as [?] is the inner row's, never the big line's.
  it("gives a word to the smallest line holding it, and saves it over that line alone", () => {
    const blob = flag("line", "[?]", BIG);
    const row = flag("line", "[?]", ROW);
    const rowMark = flag("mark", "[?]", ROW);
    const blobRow = { text: "[?]", region: blob.region };
    const innerRow = { text: "[?]", region: row.region };
    const rows = [blobRow, innerRow];
    expect(fixesFor(rowMark, [blob, row, rowMark], innerRow)).toEqual([rowMark, row]); // never blob
    expect(wordToFix([blob, rowMark], blobRow, rows)).toBe(blob);
    expect(wordToFix([blob, row, rowMark], innerRow, rows)).toBe(rowMark);
    expect(panel).toContain("const toFix = first && onFix ? wordToFix(flags, line, lines) : undefined;");
    expect(panel).toContain("const row = shown[index]; // #614: the line the Save came from");
    expect(panel).toContain("for (const each of fixesFor(flag, flags, row)) if (done.ok)");
  });

  it("shows a line's fix in that line only, a word's in the line holding it", () => {
    const edited = { ...flag("line", "40", BIG), text: "45" }; // a big line read as "40", typed as "45"
    const word = { ...mark, text: "Rice" };
    expect(withFixes(inner, [edited, word])).toBe("Rice 2 40 80");
    expect(withFixes({ text: "40", region: big.region }, [edited])).toBe("45");
    expect(withFixes(bigRow, [edited])).toBe(big.as_read); // read as more than "40": another line
  });

  it("still marks the block a cited band lies in, for citations (#523)", () => {
    const block = { region: region([0.1, 0.2, 0.9, 0.7]) };
    expect(lineMarked(block, region([0.1, 0.2, 0.9, 0.25]))).toBe(true); // a value on its first band
    expect(lineMarked(block, region([0.1, 0.75, 0.9, 0.8]))).toBe(false); // below it
    expect(lineMarked({ region: region(ROW) }, region(BIG))).toBe(true); // a line in a cited box
  });
});

describe("#614 one fix box open at a time", () => {
  it("keys the box by its row and word, so rows sharing a flag never open together", () => {
    const rows = [inner, middle, bigRow];
    const open = fixBoxKey(1, big); // the second row opened, as if it offered big
    expect(rows.map((_, index) => fixBoxKey(index, big) === open)).toEqual([false, true, false]);
    expect(panel).toContain("const key = fixBoxKey(index, flag);");
    expect(panel).toContain("const box = toFix && fixing?.key === fixBoxKey(index, toFix) ? fixing : null;");
    // The field (and its ref) is drawn only under the open row.
    expect(panel).toMatch(/\{toFix && ask && box && \(\s*<form/);
    expect(panel.match(/ref=\{fixInput\}/g)).toHaveLength(1);
  });
});
