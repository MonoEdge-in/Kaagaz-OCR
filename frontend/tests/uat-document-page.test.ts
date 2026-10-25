// Vrushit's UAT of the document page (25 Oct 2026), one block an item: the ⋯ menu's order and
// Export as Data, no translation (read-clearly.test.ts), one compact value row, no Invoice
// section, Fix text for a [?] in the Text view, and a Needs attention count that matches the marks.

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import * as panels from "@/app/(app)/documents/[id]/DocumentPanels";
import { AttentionPanel, attentionCount } from "@/app/(app)/documents/[id]/DocumentPanels";
import { documentMoreItems } from "@/app/(app)/documents/[id]/WordReview";
import { dataText } from "@/components/viewer/clearCopy";
import { withFixes } from "@/components/viewer/scanned";
import type { PageSummary, ReadLine, ReviewStatus, WordFlag } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
const VIEW = "app/(app)/documents/[id]/DocumentView.tsx";
const VIEW_CSS = "app/(app)/documents/[id]/DocumentView.module.css";

const box = (x0: number, y0: number, x1: number, y1: number) => ({ kind: "box" as const, page: 1, box: [x0, y0, x1, y1] as [number, number, number, number] });

describe("1. the ⋯ menu: Export as Data and Download original above Move to Trash", () => {
  it("draws the document's own actions, then the review's (Export as Data, Download original), then Move to Trash", () => {
    const menu = read("components/documents/DocumentActions.tsx");
    const own = menu.indexOf("{entries.filter((entry) => !isDanger(entry)).map(entryItem)}");
    const more = menu.indexOf("{more.map((extra, index) => (");
    const trash = menu.indexOf("{entries.filter(isDanger).map(entryItem)}");
    expect(own).toBeGreaterThan(0);
    expect(more).toBeGreaterThan(own);
    expect(trash).toBeGreaterThan(more);
  });

  it("names it Export as Data and says in plain words what it saves", () => {
    const status = { status: "ready", ensemble: { allowed: false, reason: null, run: null } } as unknown as ReviewStatus;
    const items = documentMoreItems(status, { onEnsemble: () => undefined, onExport: () => undefined, download: "/f" });
    expect(items.map((item) => [item.label, item.hint])).toEqual([
      ["Export as Data", "Saves the text as read, page by page, and the values found, as a plain text file (.txt)."],
      ["Download original", "Save the file exactly as it was uploaded."],
    ]);
  });

  it("builds the .txt from the clean copy, page by page, then the values found", () => {
    const line = (text: string): ReadLine => ({ text, confidence: null, origin: "ocr", kind: "paragraph", region: box(0, 0, 1, 0.1) });
    const copy = { pages: [{ page_no: 1, lines: [line("Bill 471"), line("Total [?]")], marks: [] }, { page_no: 2, lines: [line("Thank you")], marks: [] }], truncated: false };
    const values = [{ field: "invoice_date", value: "19/03/22", region: box(0, 0, 1, 0.1) }];
    expect(dataText(copy, values)).toBe(
      "Page 1 of 2\nBill 471\nTotal [?]\n\nPage 2 of 2\nThank you\n\nValues found\nInvoice date: 19/03/22 (page 1)",
    );
    expect(dataText({ ...copy, truncated: true }, [])).toMatch(/Thank you\n\nThis document is long, so the clean copy stops here\./);
    const view = read("app/(app)/documents/[id]/WordReview.tsx");
    expect(view).toContain('{ type: "text/plain;charset=utf-8" }');
    expect(view).toContain('`${detail.display_name.replace(/\\.[^.]+$/, "")}.txt`');
  });
});

describe("3. a value is one compact row in 0.75rem, with no What was read", () => {
  it("has no What was read disclosure anywhere on the document page", () => {
    expect(existsSync(join(ROOT, "components/viewer/WhatWasRead.tsx"))).toBe(false);
    expect(read(VIEW)).not.toContain("WhatWasRead");
    expect(read("app/(app)/documents/[id]/DocumentPanels.tsx")).not.toContain("WhatWasRead");
    expect(css("components/viewer/ScannedText.module.css")).not.toMatch(/\.readList|\.read summary/);
  });

  it("sets the row's text at 0.75rem and lays a narrow row out on one line, its badge at the end", () => {
    const sheet = css(VIEW_CSS);
    expect(sheet).toMatch(/\.valuesTable \{[^}]*font-size: 0\.75rem;/);
    expect(sheet).toMatch(/\.valuesTable td :global\(\.btn\) \{[^}]*font-size: inherit;/);
    const narrow = sheet.slice(sheet.indexOf("@container (max-width: 35.99rem)"));
    expect(narrow).toMatch(/\.valuesTable tbody tr \{\s*display: flex;\s*flex-wrap: wrap;\s*align-items: center;/);
    expect(narrow).toMatch(/\.valuesTable td\.labelCell \{\s*margin-inline-start: auto;/);
    expect(narrow).not.toMatch(/grid-template-areas/);
    expect(narrow).not.toMatch(/font-size: var\(--fs-lg\)/); // the value was 18px in a card
  });
});

describe("4. no Invoice section and no line cards on the document page", () => {
  it("has no InvoicePanel, and Details never draws the invoice", () => {
    expect("InvoicePanel" in panels).toBe(false);
    expect(read(VIEW)).not.toMatch(/InvoicePanel|invoice-heading/);
  });
});

describe("5. Fix text: replace a [?] in the Text view", () => {
  const flag = (as_read: string, text: string, region = box(0.3, 0.02, 0.4, 0.08)) =>
    ({ as_read, text, region }) as Pick<WordFlag, "as_read" | "text" | "region">;
  const line = { text: "spitting out [?] text. I was", region: box(0, 0, 1, 0.1) };

  it("shows the word a person typed in place of the [?] in its line, and nothing else", () => {
    expect(withFixes(line, [flag("[?]", "garbled")])).toBe("spitting out garbled text. I was");
    expect(withFixes(line, [flag("[?]", "[?]")])).toBe(line.text); // not fixed yet
    expect(withFixes(line, [flag("[?]", "x", box(0.3, 0.5, 0.4, 0.6))])).toBe(line.text); // another line's
    expect(withFixes(line, [flag("[?]", "$& $1")])).toBe("spitting out $& $1 text. I was"); // typed as is
  });

  it("offers Fix text on a line holding a flagged word, opening that word's review (its Edit) for a reviewer", () => {
    const panel = read("components/viewer/ScannedTextPanel.tsx");
    expect(panel).toContain("const toFix = first && onFix ? flags.find((each) => lineMarked(each, line.region)) : undefined;");
    expect(panel).toMatch(/onClick=\{\(\) => onFix\(toFix\)\}>\s*Fix text/);
    const view = read(VIEW);
    expect(view).toContain("onFix={review.status?.can_review && detail.deleted_at === null ? startReview : undefined}");
    expect(view).toContain("fixed={review.flags.filter((flag) => flag.page_no === page.page_no)}");
  });
});

describe("6. Needs attention counts what is marked, in a filled circle", () => {
  const page: PageSummary = {
    page_no: 1,
    width: 595,
    height: 842,
    unit: "px",
    has_image: true,
    route: "ocr",
    effective_dpi: 300,
    low_resolution: true,
    read_by: null,
    read_quality: "uncertain",
    read_issues: ["low_resolution"],
    unsure_words: 2,
    illegible_words: 0,
    unpinned_lines: 0,
    accepted_by: null,
    accepted_at: null,
    read_again_requested_at: null,
    problems: [{ code: "low_resolution", says: "This page was scanned at low quality, so small letters may be misread.", fix: "Scan it again." }],
  };

  it("says 2 to check for a page with one problem line and two marked words (it said 1)", () => {
    expect(attentionCount([page])).toBe(2);
    const html = renderToStaticMarkup(
      createElement(AttentionPanel, { status: "ready", pages: [page], current: null, marks: [], onPage: () => undefined, onMark: () => undefined }),
    );
    expect(html).toContain("2 to check");
    expect(html).toContain("2 reading problems on page 1.");
  });

  it("draws the tab's count as a small filled circle in the review colour, from tokens", () => {
    expect(read(VIEW)).toContain('data-alert={item.id === "attention" ? "true" : undefined}');
    const rule = /\.tabCount\[data-alert\] \{([^}]*)\}/.exec(css(VIEW_CSS))?.[1] ?? "";
    expect(rule).toMatch(/border-radius: var\(--k-r-pill\);/);
    expect(rule).toMatch(/background: var\(--k-review\);/);
    expect(rule).toMatch(/color: var\(--k-surface\);/);
    expect(rule).toMatch(/min-width: 1\.25rem;[\s\S]*height: 1\.25rem;/);
  });
});
