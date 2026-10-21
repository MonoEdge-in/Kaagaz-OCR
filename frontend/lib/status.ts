// The handoff's six states (docs/architecture.md, The screens), and which backend
// values feed each. This is the only place the mapping lives: a screen asks
// here, then draws the StatusBadge (components/ui/StatusBadge) or, for a
// document, the status chip.

import type { Answer, DocumentStatus, Label, LabelReason, ReadQuality } from "./types";

export type UiStatus = "verified" | "review" | "from" | "notfound" | "reading" | "failed";

/**
 * The words on each badge. Always shown, so colour is never the only signal.
 * The review state says "Ready to verify", not the handoff's words (Tushar, 16
 * Oct 2026, guarded by tests/ready-to-verify.test.ts).
 */
export const STATUS_WORDS: Record<UiStatus, string> = {
  verified: "Verified",
  review: "Ready to verify",
  from: "From documents",
  notfound: "Not found",
  reading: "Reading",
  failed: "Failed",
};

// --- Values -----------------------------------------------------------------------

// Why a value is in the review state, in plain words, from its label_reason.
const REVIEW_REASONS: Partial<Record<LabelReason, string>> = {
  readers_disagree: "Two readers read this differently",
  unsure_text: "Hard to read",
  low_confidence: "Hard to read",
  ocr_script: "Hard to read",
  box_unanchored: "The exact spot isn't confirmed",
  check_failed: "The totals don't add up",
  single_reader: "Only one reader saw this",
  model_chosen: "Picked by Kaagaz, check the page",
  local_reader: "Read by the backup reader",
};

const FIXED_BY_REVIEWER = "Fixed by a reviewer";

export interface ValueStatus {
  state: "verified" | "review";
  reason: string | null; // null when there is nothing to add to the badge
}

/** A value's badge: its label, with the reason in words when there is one. */
export function valueStatus(label: Label, reason: LabelReason): ValueStatus {
  if (reason === "corrected" || reason === "learned_correction") {
    return { state: "verified", reason: FIXED_BY_REVIEWER };
  }
  if (label === "verified") return { state: "verified", reason: null };
  return { state: "review", reason: REVIEW_REASONS[reason] ?? null };
}

// --- Answers ----------------------------------------------------------------------

/**
 * An answer's badge. "unchecked" means the figure asked for is only in values
 * still to confirm, so it is in the review state.
 */
export function answerStatus(answer: Pick<Answer, "status" | "label">): UiStatus {
  if (answer.status === "not_found") return "notfound";
  if (answer.status === "unchecked") return "review";
  if (answer.label === "verified") return "verified";
  if (answer.label === "needs_review") return "review";
  return "from";
}

// --- Pages ------------------------------------------------------------------------

/** A page's badge, or null for a page read well (it carries none). */
export function pageStatus(quality: ReadQuality): UiStatus | null {
  if (quality === "unreadable") return "failed";
  if (quality === "uncertain") return "review";
  return null;
}

// --- Documents --------------------------------------------------------------------

// What the person can do about a failed or refused file, kept per failure_code.
// The server's status_message already says what happened; this is the one line
// on what to do next, for the place that shows it on its own (a badge's reason).
const FIXES: Record<string, string> = {
  too_many_pages: "Split it into parts and upload each one.",
  retries_exhausted: "Read it again, or ask your admin to look at it.",
  unreadable: "Upload a sharper scan.",
  ocr_failed: "Read it again, or upload a clearer scan.",
  customer_disabled: "Read it again.",
  virus_found: "Upload a clean copy of the file.",
  unscannable: "Save it again and upload that copy.",
  blocked_content: "Save it again as a plain PDF, photo or spreadsheet and upload that.",
  needs_reader: "Upload it as a PDF, photo or spreadsheet.",
};
const ANY_FIX = "Read it again, or try another copy of it.";

/**
 * The fix line for a failed or refused file. needs_reader has no failure_code,
 * so its status stands in for one.
 */
export function failureFix(status: DocumentStatus, failureCode: string | null): string {
  const code = status === "needs_reader" ? "needs_reader" : (failureCode ?? "");
  return Object.hasOwn(FIXES, code) ? FIXES[code]! : ANY_FIX;
}

/**
 * A document's state: reading while it is checked or read, failed when it was
 * refused, failed or can't be read, and null once it is ready (a ready
 * document carries no six-state badge, only the word Ready).
 */
export function documentStatus(status: DocumentStatus): "reading" | "failed" | null {
  if (status === "received" || status === "clean") return "reading";
  if (status === "ready") return null;
  return "failed";
}

/** The word on a document's status chip. */
export function documentWords(status: DocumentStatus): string {
  const state = documentStatus(status);
  return state === null ? "Ready" : STATUS_WORDS[state];
}
