// PRD 9 G2 (F-15, F-16). F-16: a line whose spot no found box confirmed (box_source "model": on
// Mistral a block's box, shared by its lines) was highlighted as a solid box over the whole block,
// as if it were the exact place. It is a dashed box labelled "Approximate spot" now; a found line
// keeps the solid box. F-15: Read this page again said nothing about its result; when it is done it
// says "N lines changed" or "Read again: same result", from the page's lines before and after.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { readAgainText, readLines } from "@/components/viewer/reprocess";
import { APPROXIMATE_SPOT } from "@/components/viewer/selection";
import type { PageReadings, ReadLine } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const VIEW = read("app/(app)/documents/[id]/DocumentView.tsx");

describe("PRD 9 F-16 an unconfirmed spot is drawn as approximate", () => {
  it("says Approximate spot", () => {
    expect(APPROXIMATE_SPOT).toBe("Approximate spot");
  });

  it("marks a Text line's spot approximate only when its box_source is model", () => {
    expect(read("components/viewer/ScannedTextPanel.tsx")).toContain('approximate: line.box_source === "model"');
    expect(VIEW).toContain("choose({ ...selectSpot(spot.region, spot.label), approximate: spot.approximate })");
    expect(VIEW.match(/approximate=\{selection\?\.approximate\}/g)).toHaveLength(2); // the PDF and the photo viewer
  });

  it.each(["components/viewer/PageViewer.tsx", "components/viewer/PdfDocumentViewer.tsx"])(
    "%s draws the approximate spot dashed with its label, and the found spot as before",
    (path) => {
      const source = read(path);
      expect(source).toContain("styles.approximate");
      expect(source).toContain("{approximate ? <span className={styles.approximateLabel}>{APPROXIMATE_SPOT}</span> : null}");
      expect(source).toContain("approximate = false,");
    },
  );

  it("draws it dashed", () => {
    expect(read("components/viewer/PageViewer.module.css")).toMatch(/\.approximate \{\s*border-style: dashed;/);
  });
});

const line = (text: string, box_source: ReadLine["box_source"] = "found"): ReadLine => ({
  text,
  confidence: null,
  origin: "ocr",
  kind: "paragraph",
  region: { kind: "box", page: 1, box: [0.1, 0.1, 0.9, 0.15] },
  box_source,
});
const readings = (first: ReadLine[], second: ReadLine[] = []): PageReadings => ({
  page_no: 1,
  readers: [
    { role: "first", reader: "mistral/mistral-ocr-4-1", reader_version: "1", truncated: false, lines: first },
    { role: "second", reader: "rapidocr", reader_version: "1", truncated: false, lines: second },
  ],
});

describe("PRD 9 F-15 Read this page again says what changed", () => {
  it("compares the main reader's lines, each with whether its spot was confirmed", () => {
    expect(readLines(readings([line("Total 500"), line("Date", "model")], [line("other")]))).toEqual([
      "found|Total 500",
      "model|Date",
    ]);
    expect(readLines({ page_no: 1, readers: [] })).toEqual([]);
  });

  it("says the same result when nothing changed, in any order", () => {
    expect(readAgainText(["found|A", "model|B"], ["model|B", "found|A"])).toBe("Read again: same result");
    expect(readAgainText([], [])).toBe("Read again: same result");
  });

  it("counts each line changed, added or gone once", () => {
    expect(readAgainText(["found|A", "model|B", "model|C"], ["found|A", "found|B", "found|C"])).toBe("2 lines changed");
    expect(readAgainText(["found|A"], ["found|A", "found|B"])).toBe("1 line changed");
    expect(readAgainText(["found|A", "found|A"], ["found|A"])).toBe("1 line changed");
  });

  it("takes the lines before Read again is sent, and says the result once the page is read", () => {
    const problems = read("app/(app)/documents/[id]/PageProblems.tsx");
    expect(problems).toMatch(/getPageReadings\(documentId, page\.page_no\)[\s\S]*readPageAgain\(documentId, page\.page_no\)/);
    expect(VIEW).toContain("setToast(readAgainText(again.lines, readLines(after)))");
  });
});
