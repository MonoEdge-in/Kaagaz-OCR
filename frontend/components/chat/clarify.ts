// Clarifying questions in the thread (PRD 6 B2). Pure, unit tested in node (tests/chat-clarify.test.ts).

import type { Message } from "@/lib/types";

/** The open clarifying question: the newest message, when it asks one and was not left. */
export function openClarify(messages: readonly Message[], left: string | null): Message | null {
  const last = messages.at(-1);
  return last?.answer?.status === "clarify" && last.answer.clarify && last.id !== left ? last : null;
}

/**
 * Each answered clarify, by id: the labels picked ("You chose: Pune"), or null when the next
 * question was typed instead. The server stores a pick as its labels, joined by ", ".
 */
export function chosenAfter(messages: readonly Message[]): Map<string, string | null> {
  const chosen = new Map<string, string | null>();
  messages.forEach((message, index) => {
    const next = messages[index + 1];
    const labels = message.answer?.clarify?.choices.map((choice) => choice.label) ?? [];
    if (message.answer?.status !== "clarify" || next?.role !== "user") return;
    const picked = labels.includes(next.text) || next.text.split(", ").every((part) => labels.includes(part));
    chosen.set(message.id, picked ? next.text : null);
  });
  return chosen;
}

/** Typed words answer the open clarify only as a short name (the server's rule, clarify.py
 * answers_clarify): at most 60 characters and no question mark; else a new question. */
export const answersClarify = (text: string): boolean => text.length <= 60 && !text.trimEnd().endsWith("?");

/** Devanagari text is marked Marathi, so it is read and shaped as such. */
export const langOf = (text: string): string | undefined => (/[ऀ-ॿ]/.test(text) ? "mr" : undefined);

/** The labels of the choices `ids` names ("all": every pick), joined as the server stores them. */
export function pickedText(message: Message, ids: readonly string[]): string {
  const choices = message.answer?.clarify?.choices ?? [];
  const every = ids.includes("all");
  return choices
    .filter((choice) => choice.kind === "pick" && (every || ids.includes(choice.id)))
    .map((choice) => choice.label)
    .join(", ");
}

export const answerDomId = (messageId: string) => `answer-${messageId}`;
