// The banner over a problem page (PRD 5 U4) and the Attention panel (U5), kept pure so
// they are unit tested in node (tests/viewer-problems.test.ts, viewer-panels.test.ts).
// The banner says what happened in one line from the issue table (the server's
// page_summary.problems), the fix, and offers Read this page again, Upload a better
// scan and Keep as is. Read again and Keep as is ask first, inside the page; the server
// checks the role again (a reviewer or an admin, F5).

import { ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { PageProblem, PageSummary, ReadIssue, WordFlag } from "@/lib/types";

export const ALL_CLEAR = "Everything on these pages was read clearly.";
// PRD 9 F-14: said in Needs attention once nothing is left to check.
export const ALL_CHECKED = "Everything is checked";

/** The page's problems, one per issue; [] for a good page or a reply from before PR 2. */
export function problemsOf(page: Pick<PageSummary, "problems" | "read_quality">): PageProblem[] {
  return page.read_quality === "good" ? [] : (page.problems ?? []);
}

/** Said under the banner once the page was kept as is: it stays marked. null until then. */
export function keptText(page: Pick<PageSummary, "accepted_at">): string | null {
  if (page.accepted_at === null) return null;
  return `Kept as is on ${formatDate(page.accepted_at)}. It stays marked on the page and in answers.`;
}

/** Said while the page waits to be read again. */
export const READING_AGAIN_TEXT = "Being read again. This page updates as it goes.";

export type Fix = "read" | "keep";

export function fixQuestion(fix: Fix, pageNo: number): string {
  return fix === "read"
    ? `Read page ${pageNo} again, more carefully? The rest of the document is not read again.`
    : `Keep page ${pageNo} as it was read? It leaves the attention list but stays marked on the page and in answers.`;
}

/** The banner's steps. Only "confirming" may send, so a second press never sends twice. */
export type FixState =
  | { step: "idle" }
  | { step: "confirming"; fix: Fix }
  | { step: "sending"; fix: Fix }
  // The fix went through: focus goes to its note, as the confirmation it replaces is gone.
  | { step: "done"; fix: Fix }
  | { step: "error"; message: string };

export type FixEvent =
  | { type: "open"; fix: Fix }
  | { type: "cancel" }
  | { type: "send" }
  | { type: "sent" }
  | { type: "failed"; message: string };

/** The next step; an event that does not fit the current step leaves it as it is. */
export function fixReducer(state: FixState, event: FixEvent): FixState {
  switch (event.type) {
    case "open":
      return state.step === "idle" || state.step === "error" || state.step === "done"
        ? { step: "confirming", fix: event.fix }
        : state;
    case "cancel":
      return state.step === "confirming" ? { step: "idle" } : state;
    case "send":
      return state.step === "confirming" ? { step: "sending", fix: state.fix } : state;
    case "sent":
      return state.step === "sending" ? { step: "done", fix: state.fix } : state;
    case "failed":
      return state.step === "sending" ? { step: "error", message: event.message } : state;
  }
}

/** Why a fix did not go through, in plain words. */
export function fixErrorText(caught: unknown): string {
  if (caught instanceof ApiError) {
    if (caught.status === 403) return "Only a reviewer or an admin can do this.";
    if (caught.status === 409 && caught.code === "not_reprocessable") {
      return "This page can't be read again right now. Try again once the document is ready.";
    }
    return caught.message;
  }
  return "Check your internet connection, then try again.";
}

/** Said to a member beside Upload a better scan: who can do the rest (#444). */
export const KEEP_MEMBER_NOTE = "Only a reviewer or an admin can read this page again or keep it as is.";

// The page problems a word on the review list stands for: the server's FLAGGED_ISSUES and its rule
// for a page settled through its words (kaagaz/db/word_review.py, its module docstring, #443).
export const FLAGGED_ISSUES: ReadonlySet<ReadIssue> = new Set(["illegible_words", "unpinned", "readers_disagree"]);

/** A word still open on the review list, as the Attention panel names it. */
export type OpenWord = Pick<WordFlag, "page_no" | "text" | "reason_text">;

/**
 * What Needs attention asks a person to settle (#443, #444): the one rule its count, its lead line
 * and the Text panel's actions follow. Each word still open on the review list (`open`: the review
 * band only, #432), settled by Confirm or Edit; and each page on the attention list (not good, not
 * kept as is) that the server does not keep with its last word (no problem code in read_issues, or
 * one not in FLAGGED_ISSUES), or with none of its words left open, settled by Keep as is. A page kept as is leaves; its open words stay.
 * PRD 9 F-14: `open` null (the words not loaded, yet or at all): null, not known; never a number
 * from the pages' marked spots (it said 31 while the header said 11). O(p + w).
 */
export function toSettle(
  pages: readonly PageSummary[],
  open: readonly Pick<WordFlag, "page_no">[] | null,
): { count: number; pages: number[] } | null {
  if (open === null) return null;
  const listed = pages.filter((page) => page.read_quality !== "good" && page.accepted_at === null);
  const withWords = new Set(open.map((word) => word.page_no));
  const keptWithWords = (page: PageSummary) =>
    page.read_issues.length > 0 && page.read_issues.every((issue) => FLAGGED_ISSUES.has(issue));
  const toKeep = listed.filter((page) => !withWords.has(page.page_no) || !keptWithWords(page));
  return { count: open.length + toKeep.length, pages: [...new Set([...withWords, ...toKeep.map((page) => page.page_no)])] };
}

/** One page in the Attention panel: its problems in words, or its counts for a reply from before PR 2. */
export interface AttentionPage {
  pageNo: number;
  lines: string[];
  kept: boolean; // kept as is: off the attention list, still marked
  words?: number; // a good page, listed for its open words alone (#443): how many; its line names them
}

const counted = (n: number, one: string, many: string) => `${n.toLocaleString("en-IN")} ${n === 1 ? one : many}`;

/** Every problem in the document, grouped by page, in page order (U5); a good page too while a
 * word on it is still open (#443), so the list names every page toSettle counts. */
export function attentionPages(pages: readonly PageSummary[], open: readonly OpenWord[]): AttentionPage[] {
  return pages
    .filter((page) => page.read_quality !== "good" || open.some((word) => word.page_no === page.page_no))
    .map((page) => {
      if (page.read_quality === "good") {
        const words = open.filter((word) => word.page_no === page.page_no);
        const named = words.map((word) => `'${word.text}' (${word.reason_text})`).join(", ");
        const lines = [`${counted(words.length, "word", "words")} to check: ${named}.`];
        return { pageNo: page.page_no, lines, kept: page.accepted_at !== null, words: words.length };
      }
      const problems = problemsOf(page).map((problem) => problem.says);
      const lines =
        problems.length > 0
          ? problems
          : [
              page.read_quality === "unreadable" ? "This page needs a closer look." : "",
              page.unsure_words > 0 ? counted(page.unsure_words, "word unsure", "words unsure") : "",
              page.illegible_words > 0 ? `Hard to read: ${counted(page.illegible_words, "word", "words")}.` : "",
              page.unpinned_lines > 0
                ? `The exact spot of ${counted(page.unpinned_lines, "line", "lines")} needs a check.`
                : "",
            ].filter(Boolean);
      return { pageNo: page.page_no, lines, kept: page.accepted_at !== null };
    });
}
