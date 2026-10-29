// Vrushit's UAT 3 (28 Oct 2026, #443 and #444): every thing Needs attention asks a person to settle
// can be settled where it is shown, and the count clears once all are. A lone "." flagged for review
// could only be given a meaning; a page read as faint, with no word to check, counted 1 for ever.
// The one rule is toSettle (components/viewer/pageProblems.ts): the tab's count, the panel's lead
// line and the Text panel's words all come from it; the server keeps a page as is once its last
// word is settled (kaagaz/db/word_review.py, FLAGGED_ISSUES).

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AttentionPanel, attentionCount } from "@/app/(app)/documents/[id]/DocumentPanels";
import { PageProblems } from "@/app/(app)/documents/[id]/PageProblems";
import { lastOpenOnPage } from "@/components/review/wordReview";
import { ALL_CLEAR, FLAGGED_ISSUES, KEEP_MEMBER_NOTE, attentionPages, toSettle } from "@/components/viewer/pageProblems";
import { fixBox } from "@/components/viewer/scanned";
import type { PageProblem, PageSummary, WordFlag } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const VIEW = "app/(app)/documents/[id]/DocumentView.tsx";
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
const flag = (word_key: string, page_no: number, state: WordFlag["state"] = "open") => ({ word_key, page_no, state });

describe("#443 #444 toSettle: what Needs attention asks a person to settle", () => {
  // A word mark ([?]), a line not pinned and a value read differently: each is a word on the
  // review list, and the page problem it stands for is settled with it.
  it.each([
    ["a word mark", "illegible_words"],
    ["a line whose spot is not confirmed", "unpinned"],
    ["a value read differently", "readers_disagree"],
  ] as const)("counts %s once, as a word, while it is open; nothing once it is settled", (_, code) => {
    const open = page(1, { read_issues: [code], problems: [problem(code)] });
    expect(toSettle([open], on(1))).toEqual({ count: 1, pages: [1] });
    // Settled: the server kept the page as is with its last word.
    expect(toSettle([{ ...open, accepted_at: KEPT }], [])).toEqual({ count: 0, pages: [] });
    // Settled before the server kept such pages: the page is still there to keep, never lost.
    expect(toSettle([open], [])).toEqual({ count: 1, pages: [1] });
  });

  it("counts a page problem no word stands for (#444: faint) until it is kept as is", () => {
    const faint = page(1, { read_issues: ["faint"], problems: [problem("faint")] });
    expect(attentionCount([faint], [])).toBe(1);
    expect(toSettle([faint], on(1, 1))).toEqual({ count: 3, pages: [1] }); // two words and the page
    expect(attentionCount([{ ...faint, accepted_at: KEPT }], [])).toBe(0);
  });

  it("counts a page uncertain only for its unsure words (no review item, #432) once, to keep as is", () => {
    expect(attentionCount([page(2, { unsure_words: 3 })], [])).toBe(1);
    expect(attentionCount([page(2, { unsure_words: 3, accepted_at: KEPT })], [])).toBe(0);
  });

  // Review panel (29 Oct 2026): the server's rule, read from the same raw read_issues. A page with
  // no problem code (an Ensemble line flag on it) or one the issue table leaves out is still to keep.
  it("keeps a page to keep as is when the server would not keep it with its last word", () => {
    expect(toSettle([page(1)], on(1))).toEqual({ count: 2, pages: [1] }); // no problem code
    const unread = page(2, { read_issues: ["illegible_words", "read_locally"], problems: [problem("illegible_words")] });
    expect(toSettle([unread], on(2))).toEqual({ count: 2, pages: [2] }); // read_locally has no problem line
    const flagged = page(3, { read_issues: ["illegible_words", "unpinned"], problems: [] });
    expect(toSettle([flagged], on(3))).toEqual({ count: 1, pages: [3] }); // problems not sent: the codes still count
  });

  it("keeps counting a word still open on a kept or a good page: Keep as is settles the page, not its words", () => {
    expect(toSettle([page(1, { accepted_at: KEPT, problems: [problem("faint")] })], on(1))).toEqual({ count: 1, pages: [1] });
    expect(toSettle([page(3, { read_quality: "good" })], on(3))).toEqual({ count: 1, pages: [3] });
  });

  it("says nothing is left once every word is settled and every page good or kept", () => {
    expect(toSettle([page(1, { read_quality: "good" }), page(2, { accepted_at: KEPT })], [])).toEqual({ count: 0, pages: [] });
  });

  // Review panel (29 Oct 2026): the words are [] until they load, and stay [] if they cannot be,
  // so a page with five [?] said "1 to check", then 5, or 1 for good.
  it("counts each page's marked spots, or 1, while the words are not loaded (null), never the page alone", () => {
    const marked = page(1, { read_issues: ["illegible_words"], illegible_words: 5 });
    expect(attentionCount([marked], null)).toBe(5);
    expect(attentionCount([marked], on(1, 1, 1, 1, 1))).toBe(5); // the same once the five words come
    expect(attentionCount([page(2, { read_issues: ["faint"] }), page(3, { accepted_at: KEPT, unsure_words: 2 })], null)).toBe(1);
    expect(toSettle([marked, page(4, { read_quality: "good" })], null)).toEqual({ count: 5, pages: [1] });
  });

  it("is the server's FLAGGED_ISSUES, pinned to it", () => {
    const backend = readFileSync(join(ROOT, "..", "backend", "kaagaz", "db", "word_review.py"), "utf8");
    const names = /^FLAGGED_ISSUES = frozenset\(\{([^}]*)\}\)/m.exec(backend)?.[1] ?? "";
    const codes = names.split(",").map((name) => name.trim().toLowerCase());
    expect(codes.sort()).toEqual([...FLAGGED_ISSUES].sort());
  });
});

describe("#443 the Attention panel names every page it counts", () => {
  const good = page(3, { read_quality: "good" });
  const word = { page_no: 3, text: "Total Rs 4,850", reason_text: "readings differ" };
  const panel = (open: { page_no: number; text: string; reason_text: string }[]) =>
    renderToStaticMarkup(
      createElement(AttentionPanel, {
        status: "ready",
        pages: [good],
        open,
        current: null,
        marks: [],
        onPage: () => undefined,
        onMark: () => undefined,
        onCheck: () => undefined,
      }),
    );

  it("lists a good page while a word on it is open (an Ensemble line flag), with the word and Check it", () => {
    expect(attentionPages([good], [word])).toEqual([
      { pageNo: 3, lines: ["1 word to check: 'Total Rs 4,850' (readings differ)."], kept: false, words: 1 },
    ]);
    const html = panel([word]);
    expect(html).toContain("1 to check");
    expect(html).toContain("on page 3.");
    expect(html).toContain("Page 3");
    expect(html).toContain("1 word to check: &#x27;Total Rs 4,850&#x27; (readings differ).");
    expect(html).toContain(">Check it</button>");
    expect(html).not.toContain(ALL_CLEAR);
    expect(panel([word, { ...word, text: "Paid" }])).toContain(">Check them</button>");
  });

  it("leaves a good page with no open word off the list", () => {
    expect(attentionPages([good], [])).toEqual([]);
    expect(panel([])).toContain(ALL_CLEAR);
  });

  it("checks those words in review, from the first of them", () => {
    expect(read(VIEW)).toContain("onCheck={(pageNo) => startReview(openFlags.find((flag) => flag.page_no === pageNo))}");
  });
});

describe("#443 the Needs attention count and the Text panel follow the one rule", () => {
  const view = read(VIEW);

  it("counts the tab from the pages and the words still open, and gives the panel the same words", () => {
    expect(view).toContain("const openFlags = review.flags.filter((flag) => !settled(flag));");
    expect(view).toContain("const openWords = review.status === null ? null : openFlags;");
    expect(view).toContain("const attention = attentionCount(detail.pages, openWords);");
    expect(view).toMatch(/pages=\{detail\.pages\}\s+open=\{openWords\}/);
    expect(view).toContain("const pageFlags = page ? openFlags.filter((flag) => flag.page_no === page.page_no) : [];");
  });
});

// Review panel (29 Oct 2026): every word change asked for the whole document again (the document,
// its status, words, page marks and activity) and remounted the page's fix box, wiping an open Keep
// as is question; the rate limit is 120 a minute (an earlier 429).
describe("#443 a Confirm asks for the words alone, unless it may have kept its page", () => {
  const view = read(VIEW);

  it("asks for the whole document only after the last open word of a page", () => {
    const flags = [flag("a", 1), flag("b", 1), flag("c", 2), flag("d", 2, "reviewed")];
    expect(lastOpenOnPage(flags, flags[0]!)).toBe(false); // b is still open: the words alone
    expect(lastOpenOnPage(flags, flags[2]!)).toBe(true); // d is settled: the page may be kept
    // b right after a, before a's answer came: a counts as settled.
    expect(lastOpenOnPage(flags, flags[1]!, new Set(["a"]))).toBe(true);
    expect(lastOpenOnPage([flag("a", 1, "reviewed"), flag("b", 1)], flag("b", 1))).toBe(true);
  });

  it("builds one review object, whose word reload is two requests and leaves the fix box mounted", () => {
    const hook = read("app/(app)/documents/[id]/WordReview.tsx");
    expect(hook).toContain(
      "if (word && !lastOpenOnPage(loaded?.flags ?? [], word, changed.current)) return setAsks((n) => n + 1);",
    );
    // The words alone: the status and the flags, two requests.
    expect(hook).toContain("Promise.all([getReviewStatus(documentId), listFlags(documentId)])");
    expect(view).toMatch(/const review = useWordReview\([^;]*\(\) =>\s+setReloads\(\(n\) => n \+ 1\),\s+\);/);
    expect(view).not.toContain("changed: WordReview");
    expect(view).not.toContain("review={changed}");
    expect(view.match(/review=\{review\}/g)).toHaveLength(2);
    expect(view).toContain("if (done.ok || done.reload) review.reload(flag);");
    expect(read("app/(app)/documents/[id]/ReviewPanel.tsx")).toContain("    review.reload(flag);\n");
    // The fix box remounts only when the document is asked for again (reloads), never for the words.
    expect(view).toContain("key={`${page.page_no}:${reloads}`}");
  });
});

describe("#443 a flagged word in the Text view is fixed or confirmed where it is shown", () => {
  const panel = read("components/viewer/ScannedTextPanel.tsx");

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
    // Changed on purpose (owner's report, 29 Oct 2026): Confirm and Save in one group, so they wrap
    // together; each button at its own width (text-panel-lines.test.ts).
    expect(panel).toMatch(
      /Cancel\s*<\/button>\s*<div className=\{styles\.fixSettle\}>\s*<button type="button" className="btn" disabled=\{box\.busy\} onClick=\{\(\) => void saveFix\(toFix, index, true\)\}>\s*Confirm\s*<\/button>\s*<button type="submit"/,
    );
  });
});

describe("#444 a page problem with no word to check offers Keep as is to a reviewer or an admin", () => {
  const box = (shown: PageSummary, canFix: boolean) =>
    renderToStaticMarkup(
      createElement(PageProblems, {
        documentId: "d1",
        page: shown,
        userId: "u1",
        canFix,
        canUpload: true,
        onReadAgain: () => undefined,
        onKept: () => undefined,
        inline: true,
      }),
    );
  const faint = page(1, { read_issues: ["faint"], problems: [problem("faint")] });

  it("gives a reviewer or an admin Keep as is, and a member Upload with who can do the rest", () => {
    const reviewer = box(faint, true);
    expect(reviewer).toContain("Keep as is");
    expect(reviewer).toContain("Read this page again");
    expect(reviewer).not.toContain(KEEP_MEMBER_NOTE);
    const member = box(faint, false);
    expect(member).toContain("Upload a better scan");
    expect(member).not.toContain("Keep as is");
    expect(member).toContain(KEEP_MEMBER_NOTE);
  });

  it("offers Keep as is for a page with no problem in the issue table too (only unsure words)", () => {
    expect(box(page(2, { unsure_words: 3 }), true)).toContain("Keep as is");
    expect(box(page(2, { read_quality: "good" }), true)).toBe("");
  });

  it("stays shown, as kept, once kept: the warning goes only when someone keeps or re-uploads", () => {
    const kept = box({ ...faint, accepted_at: KEPT }, true);
    expect(kept).toContain("Kept as is on");
    expect(kept).not.toContain(">Keep as is<");
  });
});
