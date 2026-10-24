// Read clearly (19 Oct 2026): line grouping, the unsure marks, plain text only, the wording
// rules, the translation label, Translate following Ask, print styles and no sideways scroll.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  BUSY,
  COPIED,
  COPY_BLOCKED,
  LANGUAGES,
  LEFT_OUT,
  NOT_SURE_TIP,
  OPENED_NOTE,
  SHOW_IN,
  TRANSLATING,
  TRANSLATION_LABEL,
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
    expect(panel).toContain("visibleText(text)"); // the translation too
  });

  it("uses calm words: no Needs review, no banned words, no model or cost words", () => {
    expect(words).not.toMatch(/needs review/i);
    expect(words).not.toMatch(/couldn't|sorry|unfortunately|perfect|guaranteed/i);
    expect(words).not.toMatch(/\b(model|gemini|openrouter|cost|price|spend|budget)\b/i);
    expect([TRANSLATION_LABEL, TRANSLATING, BUSY]).toEqual([
      "Translation — the original text is above",
      "Translating…",
      "The translation service is busy right now. The exact text is still here; try again in a minute.",
    ]);
    expect(LANGUAGES.map((each) => each.name)).toEqual(["Original", "English", "हिंदी", "मराठी"]);
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

  it("labels the language control, marks its Devanagari options and puts it right above the text", () => {
    expect(SHOW_IN).toBe("Show the text in");
    expect(panel).toContain("label: <span lang={each.lang}>{each.name}</span>");
    expect(LANGUAGES.filter((each) => /[ऀ-ॿ]/.test(each.name)).map((each) => each.lang)).toEqual(["hi", "mr"]);
    expect(rule(".languages button")).toMatch(/min-height: var\(--h-lg\);/);
    const group = panel.slice(panel.indexOf("<div ref={reader} className={styles.reader}>"));
    expect(group.indexOf("<Segmented")).toBeGreaterThan(0);
    expect(group.indexOf("<Segmented")).toBeLessThan(group.indexOf("<div className={styles.copy}>{copy}</div>"));
    expect(panel).toContain('held.page.closest("main")?.scrollBy({ top: moved });'); // the page stays in place
  });

  it("aligns label and value lines, the label in medium weight", () => {
    expect(rule(".pair")).toMatch(/grid-template-columns: minmax\(0, 2fr\) minmax\(0, 3fr\);/);
    expect(rule(".pairLabel")).toMatch(/font-weight: 500;/);
    expect(rule(".copy")).toMatch(/font-size: 1\.125rem;[\s\S]*line-height: 1\.7;/);
  });
});
