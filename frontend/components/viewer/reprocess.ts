// "Read again" in the viewer header (PRD 2, X1): who is offered it, what is
// said before it starts, and the steps of the button, kept out of the
// component so they are unit tested in node (tests/viewer-reprocess.test.ts).
// The server checks the role and the status again (403 forbidden, 409
// not_reprocessable).

import { NOT_FOUND_DOCUMENT, NOT_FOUND_TEXT } from "@/components/access/words";
import { canReview } from "@/components/review/review";
import { ApiError } from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import type { DocumentStatus, DocumentSummary, Label, Role } from "@/lib/types";

const REPROCESSABLE: readonly DocumentStatus[] = ["ready", "failed", "needs_reader"];

/**
 * A reviewer or an admin, for a ready, failed or unreadable document that is not in Trash, or
 * one still being read 6 hours after it was asked for (`stuck`, issue 407: the server decides).
 * Never a locked one (PRD 7, #605): it is never read, but for one that failed being prepared, which
 * Retry only prepares again.
 */
export function canReprocess(
  role: Role,
  document: Pick<DocumentSummary, "status" | "deleted_at" | "stuck" | "locked">,
): boolean {
  return (
    canReview(role) &&
    document.deleted_at === null &&
    (document.locked !== true || document.status === "failed") &&
    (REPROCESSABLE.includes(document.status) || document.stuck === true)
  );
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
  | { type: "failed"; message: string }
  | { type: "settled" }; // the document is no longer being read

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
    case "settled":
      return state.step === "done" ? { step: "idle" } : state;
  }
}

/** Why it did not start, in plain words. */
export function reprocessErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409 && error.code === "not_reprocessable") {
      return "This document can't be read again right now. Only a ready, failed or unreadable document, or one still waiting 6 hours after it was sent, that is not in Trash can be.";
    }
    if (error.status === 403 && error.code === "forbidden") {
      return "Only a reviewer or an admin can read a document again.";
    }
    return errorWords(error);
  }
  return "Check your internet connection, then try again.";
}

/** Why the document could not be shown, and whether Try again can help: not when it is not there
 * (404). PRD 7 (#604): one 404 text for a document deleted, never there, or hidden. */
export function loadFailure(error: unknown): { message: string; retry: boolean } {
  if (error instanceof ApiError && error.status === 404) return { message: `${NOT_FOUND_DOCUMENT} ${NOT_FOUND_TEXT}`, retry: false };
  if (error instanceof ApiError) return { message: errorWords(error), retry: true };
  return { message: "Check your internet connection, then try again.", retry: true };
}

/** Still being checked or read: the viewer asks again until it is not. */
export function beingRead(status: DocumentStatus): boolean {
  return status === "received" || status === "clean";
}

/** Said once when reading ends while the viewer is open; again: after Read again. */
export function settledText(
  document: { status: DocumentStatus; status_message: string; values: readonly { label: Label }[] },
  again: boolean,
): string {
  if (document.status !== "ready") return `Reading failed. ${document.status_message}`;
  const found = document.values.length;
  const review = document.values.filter((value) => value.label === "needs_review").length;
  return (
    `${again ? "Read again" : "Reading finished"}. ` +
    `${found} ${found === 1 ? "value" : "values"} found, ${review} ready to verify.`
  );
}
