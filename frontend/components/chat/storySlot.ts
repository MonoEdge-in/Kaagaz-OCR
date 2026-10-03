// Where a document-mode question's story cards show (council 3 Oct 2026, the
// vanishing-cards fix). Pure, unit tested in node (tests/chat-story-slot.test.ts).
// The cards belong to the question's token, never to the answer succeeding:
// they show as soon as they arrive, below the pending card, then below the
// answer, or, when the answer failed or timed out, at the end of the log with
// the question they belong to. Only a newer question (a new token), opening a
// conversation or starting a new one clears them; a late reply for an older
// token is dropped.

import type { StoryMatch } from "@/lib/types";

export interface StorySlot {
  token: number;
  question: string;
  matches: StoryMatch[] | null; // null until they arrive
  answer: "pending" | "answered" | "failed";
  answerId: string | null;
}

export type StorySlotEvent =
  | { type: "ask"; token: number; question: string }
  | { type: "matches"; token: number; matches: StoryMatch[] }
  | { type: "answered"; token: number; answerId: string }
  | { type: "failed"; token: number }
  | { type: "clear" };

export function storySlot(state: StorySlot | null, event: StorySlotEvent): StorySlot | null {
  if (event.type === "clear") return null;
  if (event.type === "ask") {
    return { token: event.token, question: event.question, matches: null, answer: "pending", answerId: null };
  }
  if (state === null || state.token !== event.token) return state; // an older question's late reply
  switch (event.type) {
    case "matches":
      return { ...state, matches: event.matches };
    case "answered":
      return { ...state, answer: "answered", answerId: event.answerId };
    case "failed":
      return { ...state, answer: "failed" };
  }
}

/** Where the cards go: nowhere, below the pending card, below one answer, or at the end. */
export type StoryPlace =
  | { at: "none" }
  | { at: "pending" }
  | { at: "answer"; answerId: string }
  | { at: "end"; question: string };

export function storyPlace(state: StorySlot | null): StoryPlace {
  if (state === null || state.matches === null || state.matches.length === 0) return { at: "none" };
  if (state.answer === "pending") return { at: "pending" };
  if (state.answer === "answered" && state.answerId !== null) return { at: "answer", answerId: state.answerId };
  return { at: "end", question: state.question };
}
