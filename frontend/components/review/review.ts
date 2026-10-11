// The review queue's small decisions, kept out of the components so they are
// unit tested in node (tests/review-*.test.ts): whether a choice can be sent,
// how the queue changes after a value is resolved, where focus goes then, and its reason filter.

import { REASON_FILTER_TEXT, fieldText } from "@/components/viewer/labels";
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

/** The reason filter's choices, in the order the screen lists them (PRD 3 Y5, PRD 5 U6). */
export const REASON_FILTERS: readonly { value: ReviewReason; text: string }[] = [
  { value: "check_failed", text: "An invoice check failed" },
  { value: "readers_disagree", text: "The two readers disagree" },
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
