// The review queue's small decisions, kept out of the components so they are
// unit tested in node (tests/review-*.test.ts): whether a choice can be sent,
// how the queue changes after a value is resolved, and its reason filter.

import { fieldText } from "@/components/viewer/labels";
import { checkReasonText } from "@/components/viewer/panels";
import type { ReviewChoice, ReviewItem, ReviewList, ReviewReason, Role } from "@/lib/types";

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

/** Members can't correct values; reviewers and admins can. */
export function canReview(role: Role): boolean {
  return role === "reviewer" || role === "admin";
}

/** The reason filter's choices, in the order the screen lists them (PRD 3 Y5). */
export const REASON_FILTERS: readonly { value: ReviewReason; text: string }[] = [
  { value: "check_failed", text: "An invoice check failed" },
  { value: "readers_disagree", text: "The two readers disagree" },
  { value: "low_confidence", text: "The reader was not sure" },
  { value: "single_reader", text: "Only one reader read it" },
];

/** The filter's choice from the select; "" (any reason) or anything else is no filter. */
export function parseReason(value: string): ReviewReason | null {
  return REASON_FILTERS.find((choice) => choice.value === value)?.value ?? null;
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
