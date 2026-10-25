// Read clearly (19 Oct 2026): line grouping, the unsure marks, plain text only, the wording
// rules, print styles and no sideways scroll. Vrushit UAT (25 Oct 2026): no translation for now.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  COPIED,
  COPY_BLOCKED,
  LEFT_OUT,
  NOT_SURE_TIP,
  OPENED_NOTE,
  WIDE_NOTE,
  blocks,
  copyText,
  pageTitle,
  pairParts,
  parts,
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
    // Changed on purpose (Vrushit UAT, 25 Oct 2026): no translation, the one text this panel put
    // through visibleText itself; every line still goes through parts (visibleText).
  });

  it("uses calm words: no Needs review, no banned words, no model or cost words", () => {
    expect(words).not.toMatch(/needs review/i);
    expect(words).not.toMatch(/couldn't|sorry|unfortunately|perfect|guaranteed/i);
    expect(words).not.toMatch(/\b(model|gemini|openrouter|cost|price|spend|budget)\b/i);
  });

  // Changed on purpose (Vrushit UAT, 25 Oct 2026): translation is removed for now, so the
  // "Show the text in: Original / English / हिंदी / मराठी" switch and the code only it used are gone.
  it("has no translation: no language switch, no Translate, no call to translate a page", () => {
    expect(panel).not.toMatch(/Show the text in|Segmented|translatePage|canTranslate|TRANSLAT/);
    expect(read("components/viewer/clearCopy.ts")).not.toMatch(/translateFailure|LANGUAGES|SHOW_IN|TRANSLAT/);
    expect(read("lib/api.ts")).not.toContain("/translate`");
    expect(read("app/(app)/documents/[id]/DocumentView.tsx")).not.toMatch(/canTranslate|askLevel/);
    expect(read("components/viewer/ReadClearly.module.css")).not.toMatch(/\.languages|\.translation/);
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

  // Changed on purpose (Vrushit UAT, 25 Oct 2026): "never sends the same page twice" was about
  // translating pages; with no translation there is nothing in flight to hold.
});

// Document panel UX (Tushar, 25 Oct 2026): the clean copy's Text tab, readable at 320px.
describe("the clean copy, easy to read", () => {
  const panel = read("components/viewer/ReadClearlyPanel.tsx");
  const css = read("components/viewer/ReadClearly.module.css").replace(/\/\*[\s\S]*?\*\//g, "");
  const rule = (selector: string) =>
    new RegExp(`(^|\\n)${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\{([^}]*)\\}`).exec(css)?.[2] ?? "";

  it("has no all-caps labels: Copy all and Print are quiet sentence-case buttons", () => {
    expect(css).not.toMatch(/text-transform:\s*uppercase/);
    expect(panel).toMatch(/className="btn btn-quiet"[\s\S]{0,300}>\s*Copy all\s*</);
    expect(panel).toMatch(/className="btn btn-quiet" disabled=\{pages\.length === 0\} onClick=\{\(\) => window\.print\(\)\}>\s*Print\s*</);
    expect([COPIED, COPY_BLOCKED]).toEqual(["Copied", "Copying is blocked here. Select the text and copy it instead."]);
    expect(panel).toContain("<Toast message={toast} onClose={() => setToast(null)} />");
  });

  it("keeps a wide table in its own sideways frame at 320px, whole words in every cell", () => {
    expect(rule(".tableBox")).toMatch(/position: relative;[\s\S]*max-width: 100%;[\s\S]*overflow-x: auto;/);
    const cells = rule(".table th,\n.table td");
    expect(cells).toMatch(/min-width: 6rem;/);
    expect(cells).toMatch(/overflow-wrap: normal;/); // the copy's "anywhere" broke headers into letters
    expect(cells).toMatch(/word-break: keep-all;/);
    expect(rule(".tableBox[data-wide]")).toMatch(/box-shadow: inset/);
    expect(WIDE_NOTE).toBe("Scroll sideways to see all columns");
    expect(panel).toMatch(/<ActionDialog title=\{title\}[\s\S]{0,200}styles\.wideTable/);
    expect(panel).toMatch(/>\s*Open wide\s*</);
  });

  it("gives each page one heading, and a page jump when there are several", () => {
    expect([pageTitle(1, 1), pageTitle(2, 4)]).toEqual(["Page 1", "Page 2 of 4"]);
    expect(panel.match(/pageTitle\(page\.page_no, pages\.length\)\}<\/h3>/g)).toHaveLength(1);
    expect(panel).not.toContain("pageBreak");
    expect(panel).toMatch(/\{pages\.length > 1 && \(\s*<label className=\{styles\.jump\}>\s*Go to page/);
  });

  it("marks the line opened with the page's highlight and says so", () => {
    expect(OPENED_NOTE).toBe("The line you opened");
    expect(panel).toContain("{pressed && <span className={styles.opened}>{OPENED_NOTE}</span>}");
    expect(rule('.line[aria-pressed="true"]::before')).toMatch(/background: var\(--hl-fill\);/);
    expect(read("components/viewer/PageViewer.module.css")).toContain("background: var(--hl-fill);");
  });

  // Changed on purpose (Vrushit UAT, 25 Oct 2026): the language control is gone (see "has no translation").

  it("aligns label and value lines, the label in medium weight", () => {
    expect(rule(".pair")).toMatch(/grid-template-columns: minmax\(0, 2fr\) minmax\(0, 3fr\);/);
    expect(rule(".pairLabel")).toMatch(/font-weight: 500;/);
    expect(rule(".copy")).toMatch(/font-size: 1\.125rem;[\s\S]*line-height: 1\.7;/);
  });
});
