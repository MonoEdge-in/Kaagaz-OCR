// "Read again" in the viewer header (PRD 2, X1): who is offered it, what is
// said before it starts, and the steps of the button, kept out of the
// component so they are unit tested in node (tests/viewer-reprocess.test.ts).
// The server checks the role and the status again (403 forbidden, 409
// not_reprocessable).

import { canReview } from "@/components/review/review";
import { ApiError } from "@/lib/api";
import type { DocumentStatus, DocumentSummary, Role } from "@/lib/types";

const REPROCESSABLE: readonly DocumentStatus[] = ["ready", "failed", "needs_reader"];

/** A reviewer or an admin, for a ready, failed or unreadable document that is not in Trash. */
export function canReprocess(
  role: Role,
  document: Pick<DocumentSummary, "status" | "deleted_at">,
): boolean {
  return canReview(role) && document.deleted_at === null && REPROCESSABLE.includes(document.status);
}

/**
 * Said in the page before it starts. The first sentence is the PRD's (X1);
 * the second is council AR15(a): the document leaves search meanwhile.
 * `fixes` is the detail's reviewer_fixes.
 */
export function reprocessConfirmText(fixes: number): string {
  const first =
    fixes > 0
      ? `Reviewer fixes on this document (${fixes}) come back only if they were saved as remembered fixes.`
      : "This document has no reviewer fixes, so none are lost.";
  return `${first} While it is read again, and if reading fails, this document is out of search until it is read successfully.`;
}

/** The button's steps. Only "confirming" may send, so a second press never sends twice. */
export type ReprocessState =
  | { step: "idle" }
  | { step: "confirming" }
  | { step: "sending" }
  | { step: "done" }
  | { step: "error"; message: string };

export type ReprocessEvent =
  | { type: "open" } // "Read again" pressed: ask first
  | { type: "cancel" }
  | { type: "send" } // confirmed
  | { type: "sent" }
  | { type: "failed"; message: string };

/** Whether confirming now would send the request: only from the question. */
export function canSend(state: ReprocessState): boolean {
  return state.step === "confirming";
}

/** The next step. An event that does not fit the current step leaves it as it is. */
export function reprocessReducer(state: ReprocessState, event: ReprocessEvent): ReprocessState {
  switch (event.type) {
    case "open":
      return state.step === "idle" || state.step === "error" ? { step: "confirming" } : state;
    case "cancel":
      return state.step === "confirming" ? { step: "idle" } : state;
    case "send":
      return canSend(state) ? { step: "sending" } : state;
    case "sent":
      return state.step === "sending" ? { step: "done" } : state;
    case "failed":
      return state.step === "sending" ? { step: "error", message: event.message } : state;
  }
}

/** Why it did not start, in plain words. */
export function reprocessErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409 && error.code === "not_reprocessable") {
      return "This document can't be read again right now. Only a ready, failed or unreadable document that is not in Trash can be.";
    }
    if (error.status === 403 && error.code === "forbidden") {
      return "Only a reviewer or an admin can read a document again.";
    }
    return error.message;
  }
  return "Check your internet connection, then try again.";
}
