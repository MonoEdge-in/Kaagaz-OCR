// Vrushit's UAT 3 (28 Oct 2026, #443 and #444): every thing Needs attention asks a person to settle
// can be settled where it is shown, and the count clears once all are. A lone "." flagged for review
// could only be given a meaning; a page read as faint, with no word to check, counted 1 for ever.
// The one rule is toSettle (components/viewer/pageProblems.ts): the tab's count, the panel's lead
// line and the Text panel's words all come from it; the server keeps a page as is once its last
// word is settled (kaagaz/db/word_review.py, FLAGGED_ISSUES).

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { attentionCount } from "@/app/(app)/documents/[id]/DocumentPanels";
import { toSettle } from "@/components/viewer/pageProblems";
import { FIX_MEMBER_NOTE, fixBox } from "@/components/viewer/scanned";
import type { PageProblem, PageSummary } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const VIEW = "app/(app)/documents/[id]/DocumentView.tsx";
const PANEL = "components/viewer/ScannedTextPanel.tsx";
const KEPT = "2026-10-28T10:00:00Z";

const problem = (code: PageProblem["code"]): PageProblem => ({ code, says: `${code} says`, fix: `${code} fix` });
const page = (page_no: number, more: Partial<PageSummary> = {}): PageSummary => ({
  page_no,
  width: 595,
  height: 842,
  unit: "px",
  has_image: true,
  route: "ocr",
  effective_dpi: 300,
  low_resolution: false,
  read_by: null,
  read_quality: "uncertain",
  read_issues: [],
  unsure_words: 0,
  illegible_words: 0,
  unpinned_lines: 0,
  accepted_by: null,
  accepted_at: null,
  read_again_requested_at: null,
  problems: [],
  ...more,
});
const on = (...pages: number[]) => pages.map((page_no) => ({ page_no }));

describe("#443 #444 toSettle: what Needs attention asks a person to settle", () => {
  // A word mark ([?]), a line not pinned and a value read differently: each is a word on the
  // review list, and the page problem it stands for is settled with it.
  it.each([
    ["a word mark", "illegible_words"],
    ["a line whose spot is not confirmed", "unpinned"],
    ["a value read differently", "readers_disagree"],
  ] as const)("counts %s once, as a word, while it is open; nothing once it is settled", (_, code) => {
    const open = page(1, { problems: [problem(code)] });
    expect(toSettle([open], on(1))).toEqual({ count: 1, pages: [1] });
    // Settled: the server kept the page as is with its last word.
    expect(toSettle([{ ...open, accepted_at: KEPT }], [])).toEqual({ count: 0, pages: [] });
    // Settled before the server kept such pages: the page is still there to keep, never lost.
    expect(toSettle([open], [])).toEqual({ count: 1, pages: [1] });
  });

  it("counts a page problem no word stands for (#444: faint) until it is kept as is", () => {
    const faint = page(1, { read_issues: ["faint"], problems: [problem("faint")] });
    expect(attentionCount([faint])).toBe(1);
    expect(toSettle([faint], on(1, 1))).toEqual({ count: 3, pages: [1] }); // two words and the page
    expect(attentionCount([{ ...faint, accepted_at: KEPT }])).toBe(0);
  });

  it("counts a page uncertain only for its unsure words (no review item, #432) once, to keep as is", () => {
    expect(attentionCount([page(2, { unsure_words: 3 })])).toBe(1);
    expect(attentionCount([page(2, { unsure_words: 3, accepted_at: KEPT })])).toBe(0);
  });

  it("keeps counting a word still open on a kept or a good page: Keep as is settles the page, not its words", () => {
    expect(toSettle([page(1, { accepted_at: KEPT, problems: [problem("faint")] })], on(1))).toEqual({ count: 1, pages: [1] });
    expect(toSettle([page(3, { read_quality: "good" })], on(3))).toEqual({ count: 1, pages: [3] });
  });

  it("says nothing is left once every word is settled and every page good or kept", () => {
    expect(toSettle([page(1, { read_quality: "good" }), page(2, { accepted_at: KEPT })], [])).toEqual({ count: 0, pages: [] });
  });
});

describe("#443 the Needs attention count and the Text panel follow the one rule", () => {
  const view = read(VIEW);

  it("counts the tab from the pages and the words still open, and gives the panel the same words", () => {
    expect(view).toContain("const openFlags = review.flags.filter((flag) => !settled(flag));");
    expect(view).toContain("const attention = attentionCount(detail.pages, openFlags);");
    expect(view).toMatch(/pages=\{detail\.pages\}\s+open=\{openFlags\}/);
    expect(view).toContain("const pageFlags = page ? openFlags.filter((flag) => flag.page_no === page.page_no) : [];");
  });

  it("asks for the whole document again after a word change, so a page the server kept shows kept", () => {
    expect(view).toContain("const changed: WordReview = { ...review, reload: () => setReloads((n) => n + 1) };");
    expect(view).toMatch(/<SummaryBar\s+documentId=\{detail\.id\}\s+review=\{changed\}/);
    expect(view).toMatch(/<ReviewPanel\s+detail=\{detail\}\s+review=\{changed\}/);
  });
});

describe("#443 a flagged word in the Text view is fixed or confirmed where it is shown", () => {
  const panel = read(PANEL);

  it("says a lone mark may be a stray one, in plain words, and starts from it", () => {
    expect(fixBox({ text: "." })).toEqual({
      hint: "'.' may be a stray mark. Confirm it to leave it as read, or type what the page shows.",
      draft: ".",
    });
    expect(fixBox({ text: "--" }).hint).toContain("may be a stray mark");
    expect(fixBox({ text: "Rs." }).hint).toBe("Type the word you see on the page in place of 'Rs.'.");
    expect(fixBox({ text: "[?]" }).draft).toBe("");
  });

  it("offers Confirm between Cancel and Save, which settles each word as it reads (the review's Confirm)", () => {
    expect(panel).toMatch(
      /Cancel\s*<\/button>\s*<button type="button" className="btn" disabled=\{box\.busy\} onClick=\{\(\) => void saveFix\(toFix, index, true\)\}>\s*Confirm\s*<\/button>\s*<button type="submit"/,
    );
    expect(panel).toContain("done = await onFix(each, asRead ? each.text : typed);");
    // Three buttons in a narrow panel share the row, none squeezed inside its word (seen at 390px).
    expect(read("components/viewer/ScannedText.module.css")).toMatch(
      /\.fixActions > :global\(\.btn\) \{\s*flex: 1 1 auto;\s*padding-inline: var\(--space-2\);/,
    );
    // DocumentView's fixWord sends a text left as it reads as the review's Confirm.
    expect(read(VIEW)).toContain("const done = await saveWord(detail.id, flag, typed === flag.text ? null : typed);");
  });

  it("keeps the reviewer or admin rule, and tells a member about any marked word", () => {
    expect(read(VIEW)).toContain("onFix={review.status?.can_review && detail.deleted_at === null ? fixWord : undefined}");
    expect(FIX_MEMBER_NOTE).toBe("Only a reviewer or an admin can fix or confirm a marked word.");
  });
});
