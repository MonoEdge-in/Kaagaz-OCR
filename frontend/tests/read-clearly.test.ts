// Read clearly (19 Oct 2026): line grouping, the unsure marks, plain text only, the wording
// rules, the translation label, Translate following Ask, print styles and no sideways scroll.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  BUSY,
  LANGUAGES,
  LEFT_OUT,
  NOT_SURE_TIP,
  TOO_LONG,
  TRANSLATING,
  TRANSLATION_LABEL,
  UNUSABLE,
  blocks,
  copyText,
  pairParts,
  parts,
  translateFailure,
} from "@/components/viewer/clearCopy";
import type { ReadLine, ReadingMark } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");

let y = 0;
function line(text: string, extra: Partial<ReadLine> = {}): ReadLine {
  y += 0.05;
  return { text, confidence: null, origin: "ocr", kind: "paragraph", region: { kind: "box", page: 1, box: [0.1, y, 0.9, y + 0.04] }, ...extra };
}
const cell = (text: string, row: number, col: number, header = false) =>
  line(text, { kind: "table_cell", table: { id: "t1", row, col, header } });

describe("Read clearly: grouping", () => {
  it("keeps headings, pairs, lists, a table grid by row and column, and paragraphs, in order", () => {
    const lines = [
      line("Sale deed", { kind: "heading" }),
      line("नाव : सीता देशमुख"),
      line("first item", { kind: "list_item" }),
      line("second item", { kind: "list_item" }),
      cell("Amount", 0, 1, true),
      cell("Item", 0, 0, true),
      line("between the cells"),
      cell("1,200", 1, 1),
      cell("Rice", 1, 0),
      line("time 10:30 and http://x.test", { kind: null }),
    ];
    const out = blocks(lines);
    expect(out.map((block) => block.type)).toEqual(["heading", "pair", "list", "table", "paragraph", "paragraph"]);
    const table = out[3];
    expect(table?.type === "table" && table.rows.map((row) => row.map((c) => c.map((l) => l.text).join(" ")))).toEqual([
      ["Item", "Amount"],
      ["Rice", "1,200"],
    ]);
    const list = out[2];
    expect(list?.type === "list" && list.items.map((item) => item.text)).toEqual(["first item", "second item"]);
  });

  it("bolds a pair's label and keeps every character as read", () => {
    const pair = line("Name:   Sita");
    const [block] = blocks([pair]);
    expect(block?.type).toBe("pair");
    const shown = pairParts(pair, [], block?.type === "pair" ? block.label : 0);
    expect(shown.map((part) => part.text).join("")).toBe("Name:   Sita");
    expect(shown.filter((part) => part.label).map((part) => part.text)).toEqual(["Name:"]);
  });
});

describe("Read clearly: words not read for sure", () => {
  it("marks an unsure word inside the line, every [?], and a whole line its reader was unsure of", () => {
    const text = line("Total 1180 [?] paid");
    const mark: ReadingMark = { quality: "unsure", text: "1180", region: { kind: "box", page: 1, box: [0.3, y, 0.4, y + 0.04] } };
    const elsewhere: ReadingMark = { ...mark, region: { kind: "box", page: 1, box: [0.3, 0.9, 0.4, 0.95] } };
    expect(parts(text, [mark, elsewhere])).toEqual([
      { text: "Total ", unsure: false },
      { text: "1180", unsure: true },
      { text: " ", unsure: false },
      { text: "[?]", unsure: true },
      { text: " paid", unsure: false },
    ]);
    expect(parts(line("faded", { confidence: 0.5 }), [])).toEqual([{ text: "faded", unsure: true }]);
    expect(NOT_SURE_TIP).toBe("Not read for sure — check the original");
  });

  it("shows hidden characters as read, never as markup or hidden", () => {
    const shown = parts(line("<b>bold</b>‮evil"), []);
    expect(shown).toEqual([{ text: "<b>bold</b>⟦U+202E⟧evil", unsure: false }]);
    expect(copyText([{ page_no: 1, lines: [line("<script>x</script>")] }])).toBe("Page 1 of 1\n<script>x</script>");
  });
});

describe("Read clearly: the screen", () => {
  const panel = read("components/viewer/ReadClearlyPanel.tsx");
  const words = [panel, read("components/viewer/clearCopy.ts")].join("\n");

  it("renders document text as React text only", () => {
    expect(panel).not.toMatch(/dangerouslySetInnerHTML|innerHTML|insertAdjacentHTML/);
    expect(panel).toContain("visibleText(text)"); // the translation too
  });

  it("uses calm words: no Needs review, no banned words, no model or cost words", () => {
    expect(words).not.toMatch(/needs review/i);
    expect(words).not.toMatch(/couldn't|sorry|unfortunately|perfect|guaranteed/i);
    expect(words).not.toMatch(/\b(model|gemini|openrouter|cost|price|spend|budget)\b/i);
    expect([TRANSLATION_LABEL, TRANSLATING, BUSY, UNUSABLE, TOO_LONG]).toEqual([
      "Translation — the original text is above",
      "Translating…",
      "Translation isn't available right now. Try again in a minute.",
      "This page can't be translated. The exact text is above.",
      "This page is too long to translate in one go. The exact text is above.",
    ]);
    expect(LANGUAGES.map((each) => each.name)).toEqual(["Original", "English", "हिंदी", "मराठी"]);
  });

  it("says why a page was not translated, and offers Try again only when the service is busy", () => {
    expect(translateFailure("backend_unavailable")).toEqual({ text: BUSY, retry: true });
    expect(translateFailure("rate_limited")).toEqual({ text: BUSY, retry: true });
    expect(translateFailure(undefined)).toEqual({ text: BUSY, retry: true }); // no reply at all
    expect(translateFailure("translation_unusable")).toEqual({ text: UNUSABLE, retry: false });
    expect(translateFailure("page_too_long")).toEqual({ text: TOO_LONG, retry: false });
    expect(panel).toMatch(/\{tr\.retry && \(\s*<button type="button"[^\n]*\n\s*Try again/);
  });

  it("shows Translate only when Ask is on and above None", () => {
    expect(panel).toMatch(/const showTranslate = canTranslate && !switchedOff/);
    expect(read("app/(app)/documents/[id]/DocumentView.tsx")).toContain(
      'canTranslate={askLevel !== undefined && askLevel !== "none"}',
    );
  });

  it("prints the copy alone, and never scrolls sideways", () => {
    expect(read("app/globals.css")).toMatch(/@media print \{\s*body:has\(> \.print-copy\) > :not\(\.print-copy\)/);
    const css = read("components/viewer/ReadClearly.module.css");
    expect(css).toMatch(/\.copy \{[^}]*overflow-wrap: anywhere/);
    expect(css).toMatch(/\.tableBox \{[^}]*overflow-x: auto/);
  });

  it("says calmly when a long document's copy was cut (CodeRabbit, 19 Oct 2026)", () => {
    expect(panel).toMatch(/current\.copy\.truncated && <p className=\{styles\.note\}>\{LEFT_OUT\}<\/p>/);
    expect(LEFT_OUT).not.toMatch(/couldn't|sorry|unfortunately/i);
  });

  it("never sends the same page twice: in-flight pages are held in a ref until they finish", () => {
    expect(panel).toContain("const inFlight = useRef(new Set<string>());");
    expect(panel).toMatch(/!inFlight\.current\.has\(doneKey\(lang, page\)\)/);
    expect(panel).toMatch(/inFlight\.current\.add\(doneKey\(lang, page\)\)/);
    expect(panel).toMatch(/finally \{\s*inFlight\.current\.delete\(doneKey\(lang, page\)\);/);
  });
});
