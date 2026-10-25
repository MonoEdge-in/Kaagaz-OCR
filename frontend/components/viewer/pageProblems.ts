// The banner over a problem page (PRD 5 U4) and the Attention panel (U5), kept pure so
// they are unit tested in node (tests/viewer-problems.test.ts, viewer-panels.test.ts).
// The banner says what happened in one line from the issue table (the server's
// page_summary.problems), the fix, and offers Read this page again, Upload a better
// scan and Keep as is. Read again and Keep as is ask first, inside the page; the server
// checks the role again (a reviewer or an admin, F5).

import { ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { PageProblem, PageSummary } from "@/lib/types";

export const ALL_CLEAR = "Everything on these pages was read clearly.";

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

/** One page in the Attention panel: its problems in words, or its counts for a reply from before PR 2. */
export interface AttentionPage {
  pageNo: number;
  lines: string[];
  kept: boolean; // kept as is: off the attention list, still marked
  // The spots marked on the page (Vrushit UAT, 25 Oct 2026: two marks said "1 to check"): one
  // a word unsure or [?] and a line not pinned, as the reader counted them, so its marks.
  marked: number;
}

const counted = (n: number, one: string, many: string) => `${n.toLocaleString("en-IN")} ${n === 1 ? one : many}`;

/** Every problem in the document, grouped by page, in page order (U5). */
export function attentionPages(pages: readonly PageSummary[]): AttentionPage[] {
  return pages
    .filter((page) => page.read_quality !== "good")
    .map((page) => {
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
      return {
        pageNo: page.page_no,
        lines,
        kept: page.accepted_at !== null,
        marked: page.unsure_words + page.illegible_words + page.unpinned_lines,
      };
    });
}
