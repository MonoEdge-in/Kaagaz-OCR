// The review queue's small decisions, kept out of the components so they are
// unit tested in node (tests/review-*.test.ts): whether a choice can be sent,
// how the queue changes after a value is resolved, where focus goes then, and its reason filter.

import { REASON_FILTER_TEXT, fieldText, labelText } from "@/components/viewer/labels";
import { checkReasonText } from "@/components/viewer/panels";
import type {
  LabelReason,
  ReviewChoice,
  ReviewItem,
  ReviewList,
  ReviewReason,
  Role,
  VerifyAllResult,
  VerifyAllSkip,
} from "@/lib/types";

export const MAX_TYPED_LENGTH = 500;

export type ChoiceCheck =
  | { ok: true; choice: ReviewChoice; value?: string }
  | { ok: false; message: string };

/** Checks a reviewer's choice before it is sent; the server checks it again. */
export function checkChoice(
  choice: ReviewChoice,
  typed: string,
  secondReading: string | null,
): ChoiceCheck {
  if (choice === "first") return { ok: true, choice };
  if (choice === "second") {
    return secondReading === null
      ? { ok: false, message: "There is no second reading for this value." }
      : { ok: true, choice };
  }
  const value = typed.trim();
  if (!value) return { ok: false, message: "Type the correct value first." };
  if (value.length > MAX_TYPED_LENGTH) {
    return { ok: false, message: `Keep the value under ${MAX_TYPED_LENGTH} characters.` };
  }
  return { ok: true, choice, value };
}

/** The queue once one value is resolved: that item gone, the total one less. */
export function withoutItem(list: ReviewList, valueId: string): ReviewList {
  const items = list.items.filter((item) => item.value.id !== valueId);
  const removed = list.items.length - items.length;
  return { items, total: Math.max(0, list.total - removed) };
}

/** Adds a further page of the queue, skipping anything already shown. */
export function appendItems(list: ReviewList, more: ReviewList): ReviewList {
  const known = new Set(list.items.map((item) => item.value.id));
  return {
    items: [...list.items, ...more.items.filter((item) => !known.has(item.value.id))],
    total: more.total,
  };
}

/** What the page announces after a resolve. */
export function resolvedMessage(item: ReviewItem): string {
  return `Saved. ${fieldText(item.value.field)} in ${item.document_name} is now "${item.value.value}".`;
}

/** The remaining count, announced together with the save in one polite message. */
export function leftToReview(total: number): string {
  if (total === 0) return "Nothing left to review.";
  return `${total} value${total === 1 ? "" : "s"} left to review.`;
}

export type ResolveFocus =
  | { kind: "card"; valueId: string }
  | { kind: "next-page" }
  | { kind: "empty" };

/**
 * Where focus goes once a resolved card leaves the queue, so a keyboard or
 * screen reader user is never dropped on the page body: the next card, else
 * the previous one; with no card left on screen, the next page when the server
 * has more, else the "Nothing to review" heading.
 */
export function focusAfterResolve(list: ReviewList, valueId: string): ResolveFocus {
  const ids = list.items.map((item) => item.value.id);
  const at = ids.indexOf(valueId);
  const left = ids.filter((id) => id !== valueId);
  // With the resolved card gone, the next one now sits where it was.
  const target = left[Math.max(at, 0)] ?? left.at(-1);
  if (target !== undefined) return { kind: "card", valueId: target };
  return withoutItem(list, valueId).total > 0 ? { kind: "next-page" } : { kind: "empty" };
}

/** Members can't correct values; reviewers and admins can. */
export function canReview(role: Role): boolean {
  return role === "reviewer" || role === "admin";
}

// --- Verify all on the review queue ----------------------------------------------------
// One POST /review/verify-all, never a POST per value: the server verifies each value of
// the queue the screen shows, in chunks, each one checked and audited as one resolve.

/** Verify all is offered to reviewers and admins, while the queue has a value. */
export function canVerifyAll(role: Role, total: number): boolean {
  return canReview(role) && total > 0;
}

/** Verify all's filters: the queue's own, as queueParams sends them, without paging. */
export function verifyAllFilters(documentId: string | undefined, reason: ReviewReason | null) {
  return {
    ...(documentId ? { document_id: documentId } : {}),
    ...(reason ? { reason: [reason] } : {}),
  };
}

/** The confirmation's question. */
export function verifyAllQuestion(total: number): string {
  return total === 1
    ? "Verify this 1 value as it was read? It is recorded as verified by you."
    : `Verify all ${total} values as they were read? Each is recorded as verified by you.`;
}

const SKIP_TEXT: Record<VerifyAllSkip, (count: number) => string> = {
  no_reading: (n) =>
    n === 1
      ? "1 has no reading to verify and is still in the queue."
      : `${n} have no reading to verify and are still in the queue.`,
  too_long: (n) =>
    n === 1
      ? "1 is too long to verify here and is still in the queue."
      : `${n} are too long to verify here and are still in the queue.`,
  left_queue: (n) => (n === 1 ? "1 left the queue while this ran." : `${n} left the queue while this ran.`),
};

/** "Verified 840 of 856. 16 have no reading to verify and are still in the queue." */
export function verifyAllText(result: VerifyAllResult): string {
  const total = result.verified + result.skipped.reduce((sum, skip) => sum + skip.count, 0);
  if (total === 0) return "Nothing was left to verify.";
  const skipped = result.skipped.map((skip) => SKIP_TEXT[skip.reason](skip.count));
  return [`Verified ${result.verified} of ${total}.`, ...skipped].join(" ");
}

/** The reason filter's choices, in the order the screen lists them (PRD 3 Y5, PRD 5 U6). */
export const REASON_FILTERS: readonly { value: ReviewReason; text: string }[] = [
  { value: "check_failed", text: "An invoice check failed" },
  { value: "readers_disagree", text: "Readers disagree" },
  { value: "low_confidence", text: "The reader was not sure" },
  { value: "single_reader", text: "Only one reader read it" },
  { value: "unsure_text", text: REASON_FILTER_TEXT.unsure_text },
  { value: "box_unanchored", text: REASON_FILTER_TEXT.box_unanchored },
  { value: "local_reader", text: REASON_FILTER_TEXT.local_reader },
  { value: "ocr_script", text: REASON_FILTER_TEXT.ocr_script },
];

/** "Group by document" (U6): the items shown, by document, in the order each first appears. */
export function groupByDocument(items: readonly ReviewItem[]): { id: string; name: string; items: ReviewItem[] }[] {
  const groups = new Map<string, { id: string; name: string; items: ReviewItem[] }>();
  for (const item of items) {
    const group = groups.get(item.document_id);
    if (group) group.items.push(item);
    else groups.set(item.document_id, { id: item.document_id, name: item.document_name, items: [item] });
  }
  return [...groups.values()];
}

/** The filter's choice from the select; "" (any reason) or anything else is no filter. */
export function parseReason(value: string): ReviewReason | null {
  return REASON_FILTERS.find((choice) => choice.value === value)?.value ?? null;
}

/** Review's own address: ?document_id= for one document's values, &reason= for one reason.
 * Kept in the address bar, so a document opened from a card leads back to the same list (#436). */
export function reviewHref(documentId: string | undefined, reason: ReviewReason | null): string {
  const params = new URLSearchParams();
  if (documentId) params.set("document_id", documentId);
  if (reason) params.set("reason", reason);
  const text = params.toString();
  return text ? `/review?${text}` : "/review";
}

/** GET /review's parameters for one page of the queue, filtered by `reason` when set. */
export function queueParams(
  offset: number,
  limit: number,
  documentId: string | undefined,
  reason: ReviewReason | null,
) {
  return {
    limit,
    ...(offset > 0 ? { offset } : {}),
    document_id: documentId,
    ...(reason ? { reason: [reason] } : {}),
  };
}

/** "The invoice checks failed: ..." in plain words; null when its invoice did not fail one. */
export function checkReasonsText(item: Pick<ReviewItem, "check_reasons">): string | null {
  if (item.check_reasons.length === 0) return null;
  return `The invoice checks failed: ${item.check_reasons.map(checkReasonText).join("; ")}.`;
}

// --- UI audit, 16 Oct 2026 (R2, R3, R6) ---------------------------------------------

/** R2: every card here is ready to verify, so its badge says why, in a few words. */
const REASON_TAGS: Partial<Record<LabelReason, string>> = {
  readers_disagree: "Readers disagree",
  low_confidence: "Low confidence",
  single_reader: "One reader only",
  check_failed: "Invoice check failed",
  unsure_text: REASON_FILTER_TEXT.unsure_text,
  box_unanchored: REASON_FILTER_TEXT.box_unanchored,
  local_reader: "Backup reader",
  ocr_script: "Hindi or Marathi script",
  model_chosen: "Found by the AI",
};

export function reasonTag(reason: LabelReason): string {
  return REASON_TAGS[reason] ?? labelText("needs_review");
}

/** R6: how the queue is shown. "focus" (R3) is one value at a time. */
export type QueueView = "focus" | "all" | "document";

export const QUEUE_VIEWS: readonly { value: QueueView; text: string }[] = [
  { value: "focus", text: "One at a time" },
  { value: "all", text: "All items" },
  { value: "document", text: "By document" },
];

/** R3: "3 of 15", counting the values already settled in this visit. */
export function focusProgress(done: number, index: number, total: number): string {
  return `${done + index + 1} of ${done + total}`;
}

export type FocusAction = "first" | "second" | "edit" | "skip" | "save";

/**
 * R3's keys on a card in focus mode: 1 and 2 pick a reading, E goes to the box, S skips,
 * Enter saves what is typed. Never while typing in a field (Enter there submits its own
 * form) or with Ctrl, Alt or Cmd; Enter on a button or link presses that instead.
 */
export function focusKeyAction(
  key: string,
  target: "typing" | "control" | "other",
  modifier: boolean,
): FocusAction | null {
  if (modifier || target === "typing") return null;
  switch (key) {
    case "1":
      return "first";
    case "2":
      return "second";
    case "e":
    case "E":
      return "edit";
    case "s":
    case "S":
      return "skip";
    case "Enter":
      return target === "other" ? "save" : null;
    default:
      return null;
  }
}

/**
 * R3: which card focus mode shows after the one at `index` is skipped (or settled, with
 * `list` already without it): the same place, or "more" when the server has more to
 * load there, else back to the first one left (null when none is left).
 */
export function focusIndexAfter(list: ReviewList, index: number): number | "more" | null {
  if (index < list.items.length) return index;
  if (list.items.length < list.total) return "more";
  return list.items.length > 0 ? 0 : null;
}
