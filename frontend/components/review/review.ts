// The review queue's small decisions, kept out of the components so they are
// unit tested in node (tests/review-*.test.ts): whether a choice can be sent,
// and how the queue changes after a value is resolved.

import { fieldText } from "@/components/viewer/labels";
import type { ReviewChoice, ReviewItem, ReviewList, Role } from "@/lib/types";

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
