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
const mark = flag("mark", "[?]", [0.05, 0.4, 0.1, 0.45], { reason: "low_confidence" });

describe("#614 a row's Fix text works on that row's own flags only", () => {
  it("never offers a big line's flag on the rows inside it, only on its own row", () => {
    expect(wordToFix([big], inner)).toBeUndefined();
    expect(wordToFix([big], middle)).toBeUndefined(); // even the row through its centre
    expect(wordToFix([big], { region: big.region })).toBe(big);
    expect(wordToFix([big, mark], inner)).toBe(mark); // the row's own word still
  });

  it("saves the word alone, and never one line over another line inside or around it", () => {
    expect(fixesFor(mark, [big, mark])).toEqual([mark]);
    // A blob read as [?] with a row inside it also read as [?]: two lines, each saved alone.
    const blob = flag("line", "[?]", BIG);
    const row = flag("line", "[?]", ROW);
    expect(fixesFor(blob, [blob, row])).toEqual([blob]);
    expect(fixesFor(row, [blob, row])).toEqual([row]);
    // #431 still: a signature read as [?], one spot flagged twice, settled by one Save.
    const SIGN: Box = [0.075, 0.25, 0.2, 0.28];
    const signLine = flag("line", "[?]", SIGN);
    const signMark = flag("mark", "[?]", SIGN);
    expect(fixesFor(signMark, [signLine, signMark])).toEqual([signMark, signLine]);
  });

  it("shows a line's fix in that line only, a word's in the line holding it", () => {
    const edited = { ...flag("line", "40", BIG), text: "45" }; // a big line read as "40", typed as "45"
    const word = { ...mark, text: "Rice" };
    expect(withFixes(inner, [edited, word])).toBe("Rice 2 40 80");
    expect(withFixes({ text: "40", region: big.region }, [edited])).toBe("45");
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
    const rows = [inner, middle, { region: big.region }];
    const open = fixBoxKey(1, big); // the second row opened, as if it offered big
    expect(rows.map((_, index) => fixBoxKey(index, big) === open)).toEqual([false, true, false]);
    expect(panel).toContain("const key = fixBoxKey(index, flag);");
    expect(panel).toContain("const box = toFix && fixing?.key === fixBoxKey(index, toFix) ? fixing : null;");
    // The field (and its ref) is drawn only under the open row.
    expect(panel).toMatch(/\{toFix && ask && box && \(\s*<form/);
    expect(panel.match(/ref=\{fixInput\}/g)).toHaveLength(1);
  });
});
