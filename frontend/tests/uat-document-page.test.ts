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
import { documentMoreItems, textFileName } from "@/app/(app)/documents/[id]/WordReview";
import { dataText } from "@/components/viewer/clearCopy";
import type { PageSummary, ReadLine, ReviewStatus } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
const VIEW = "app/(app)/documents/[id]/DocumentView.tsx";
const VIEW_CSS = "app/(app)/documents/[id]/DocumentView.module.css";

const box = (x0: number, y0: number, x1: number, y1: number) => ({ kind: "box" as const, page: 1, box: [x0, y0, x1, y1] as [number, number, number, number] });

// Changed on purpose (issue 692): Download original left the menu; the Download button sits beside it.
describe("1. the ⋯ menu: Export as Data above Move to Trash", () => {
  it("draws the document's own actions, then the review's (Export as Data), then Move to Trash", () => {
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
    const items = documentMoreItems(status, { onEnsemble: () => undefined, onExport: () => undefined });
    // Changed on purpose (issue 692): no Download original in the menu; the Download button sits beside it.
    expect(items.map((item) => [item.label, item.hint])).toEqual([
      ["Export as Data", "Saves the text as read, page by page, and the values found, as a plain text file (.txt)."],
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
    expect(view).toContain("`${textFileName(detail.display_name)}.txt`");
  });
});

describe("3. a value is one compact row in 0.75rem, with no What was read", () => {
  it("has no What was read disclosure anywhere on the document page", () => {
    expect(existsSync(join(ROOT, "components/viewer/WhatWasRead.tsx"))).toBe(false);
    expect(read(VIEW)).not.toContain("WhatWasRead");
    expect(read("app/(app)/documents/[id]/DocumentPanels.tsx")).not.toContain("WhatWasRead");
    expect(css("components/viewer/ScannedText.module.css")).not.toMatch(/\.readList|\.read summary/);
  });

  // Changed on purpose (Vrushit UAT 2, 27 Oct 2026, #416): the value rows are gone with Values
  // found (uat-2-document-page.test.ts).
});

describe("4. no Invoice section and no line cards on the document page", () => {
  it("has no InvoicePanel, and Details never draws the invoice", () => {
    expect("InvoicePanel" in panels).toBe(false);
    expect(read(VIEW)).not.toMatch(/InvoicePanel|invoice-heading/);
  });
});

describe("5. Fix text: replace a [?] in the Text view", () => {
  // Changed on purpose (PRD 9 F-12): the word a person typed is shown in its line by the server now,
  // as in the clean copy (backend/tests/test_api_text_fixes.py); the browser's withFixes is gone.
  // A word whose text is in its line twice (two [?]) is written where it is there once only, as the
  // search pieces and the clean copy have it (word_review.line_text).

  // Changed on purpose (Vrushit UAT 2, 27 Oct 2026, #417): Fix text opens a box under its line to
  // type the word there, saved as the review's Edit, not the review panel (uat-2-document-page.test.ts).
  it("offers Fix text on a line holding a flagged word, for a reviewer, saved as the review's Edit", () => {
    const panel = read("components/viewer/ScannedTextPanel.tsx");
    // Changed on purpose (#431): the line's word mark before the line's own flag (wordToFix), and
    // Fix text only while the box is shut (fix-text-431.test.ts).
    // Changed on purpose (#614): wordToFix is given the page's lines (a word is the smallest's).
    expect(panel).toContain("const toFix = first && onFix ? wordToFix(flags, line, lines) : undefined;");
    // Changed on purpose (#614): the box is the row's own (its index), so one box opens at a time.
    expect(panel).toMatch(/onClick=\{\(\) => openFix\(toFix, ask\.draft, index\)\}\s*>\s*Fix text/);
    const view = read(VIEW);
    expect(view).toContain("onFix={review.status?.can_review && detail.deleted_at === null ? fixWord : undefined}");
    // Changed on purpose (PRD 9 F-12): the fixes come with the lines from the server (the changes in
    // force: a person's, never an Ensemble proposal no person took), so nothing is laid over them here.
    expect(view).not.toContain("fixed={review.flags");
  });

  it("names the .txt after the document without its uploaded extension, and says it is preparing", () => {
    expect(textFileName("Sale deed 2024.pdf")).toBe("Sale deed 2024");
    expect(textFileName("Invoice v1.2")).toBe("Invoice v1.2");
    expect(textFileName(".pdf")).toBe(".pdf");
    expect(textFileName("   ")).toBe("document");
    expect(read(VIEW)).toContain('setToast("Preparing the text file…");');
    expect(read("app/(app)/documents/[id]/WordReview.tsx")).toContain("setTimeout(() => URL.revokeObjectURL(href), 10_000);");
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

  // Changed on purpose (#443, #444): the count is what is left to settle (toSettle). The two unsure
  // words are no review item (#432), so this page is one thing to settle (Keep as is), plus each
  // word still open on the review list.
  it("says 1 to check for a page with a problem no word stands for, and adds each word still open", () => {
    expect(attentionCount([page], [])).toBe(1);
    expect(attentionCount([page], [{ page_no: 1 }, { page_no: 1 }])).toBe(3);
    const html = renderToStaticMarkup(
      createElement(AttentionPanel, { status: "ready", pages: [page], open: [{ page_no: 1, text: "Rs 4,850", reason_text: "smudged print", region: box(0, 0, 1, 0.1), as_read: "Rs 4,850" }], current: null, marks: [], onPage: () => undefined, onMark: () => undefined, onCheck: () => undefined }),
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
