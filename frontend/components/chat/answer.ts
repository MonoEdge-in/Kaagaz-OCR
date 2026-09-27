// The chat screen's small decisions, kept out of the components so they are
// unit tested in node (tests/chat-*.test.ts): which source card a citation
// number points at, the words for an answer's label, when to show how the
// question was understood, and what the question box sends.

import { pageLink } from "@/lib/regions";
import type { Answer, AnswerSentence, Citation, Label, Message, Region } from "@/lib/types";

export const NOT_FOUND_TEXT = "I couldn't find this in your documents.";
export const MAX_QUESTION_LENGTH = 1000;
export const MAX_REASON_LENGTH = 500;

/** Citation number to its source card. */
export function citationMap(answer: Pick<Answer, "citations">): Map<number, Citation> {
  return new Map(answer.citations.map((citation) => [citation.n, citation]));
}

/** The numbers a sentence cites that have a card, in order, each once. */
export function sentenceCitations(sentence: AnswerSentence, cards: Map<number, Citation>): number[] {
  const seen = new Set<number>();
  const numbers: number[] = [];
  for (const n of sentence.citations) {
    if (cards.has(n) && !seen.has(n)) {
      seen.add(n);
      numbers.push(n);
    }
  }
  return numbers;
}

/** The DOM id of a source card, unique across the thread. */
export function cardId(messageId: string, n: number): string {
  return `source-${messageId}-${n}`;
}

export interface AnswerLabelText {
  title: string;
  explanation: string;
}

/** The answer's label in words, with one line saying what it means. */
export function answerLabelText(label: Label): AnswerLabelText {
  if (label === "verified") {
    return {
      title: "Verified",
      explanation: "Every source behind this answer was checked when the document was read.",
    };
  }
  return {
    title: "Needs review",
    explanation: "At least one source was not fully checked. Open the page before you rely on it.",
  };
}

const loose = (text: string) =>
  text
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();

/**
 * What to show after "Understood as:", or null. Only when the backend's reading
 * really differs from what was typed: case, spaces and punctuation don't count.
 */
export function understoodAs(question: string, normalized: string): string | null {
  const reading = normalized.trim();
  if (!reading) return null;
  return loose(question) === loose(reading) ? null : reading;
}

/** For each assistant message, the user question just before it. */
export function questionsByAnswer(messages: readonly Message[]): Map<string, string> {
  const questions = new Map<string, string>();
  let last: string | null = null;
  for (const message of messages) {
    if (message.role === "user") last = message.text;
    else if (last !== null) questions.set(message.id, last);
  }
  return questions;
}

/**
 * The region a source card previews and links to, on the cited page. Boxes on
 * that page are merged into the one box that covers them all, so an answer
 * quoting two lines highlights both. A cell stays the cell it is.
 */
export function citationRegion(citation: Pick<Citation, "regions" | "page_no">): Region | undefined {
  const onPage = citation.regions.filter((region) => region.page === citation.page_no);
  const boxes = onPage.filter((region) => region.kind === "box");
  if (boxes.length > 1) {
    return {
      kind: "box",
      page: citation.page_no,
      box: [
        Math.min(...boxes.map((b) => b.box[0])),
        Math.min(...boxes.map((b) => b.box[1])),
        Math.max(...boxes.map((b) => b.box[2])),
        Math.max(...boxes.map((b) => b.box[3])),
      ],
    };
  }
  return onPage[0] ?? citation.regions[0];
}

/** "Open page": the document screen at the cited page, with the spot highlighted. */
export function citationLink(citation: Citation): string {
  return pageLink(citation.document_id, citation.page_no, citationRegion(citation));
}

/** Enter sends; Shift+Enter makes a new line; typing with an input method never sends. */
export function sendsOnKey(event: { key: string; shiftKey: boolean; isComposing: boolean }): boolean {
  return event.key === "Enter" && !event.shiftKey && !event.isComposing;
}

/** The question to send, or null when there is nothing to send. */
export function cleanQuestion(text: string): string | null {
  const question = text.trim();
  if (!question) return null;
  return question.slice(0, MAX_QUESTION_LENGTH);
}

/** A down vote's reason: optional, trimmed, at most 500 characters. */
export function cleanReason(text: string): string | undefined {
  const reason = text.trim();
  return reason ? reason.slice(0, MAX_REASON_LENGTH) : undefined;
}
