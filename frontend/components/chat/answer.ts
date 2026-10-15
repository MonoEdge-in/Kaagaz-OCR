// The chat screen's small decisions, kept out of the components so they are
// unit tested in node (tests/chat-*.test.ts): which source card a citation
// number points at, the words for an answer's label, when to show how the
// question was understood, what a source card may still show of its
// document, and what the question box sends or gets back.

import { pageLink } from "@/lib/regions";
import type { Answer, AnswerSentence, Citation, Label, Message, Region } from "@/lib/types";

export const NOT_FOUND_TEXT = "I couldn't find this in your documents.";
export const MAX_QUESTION_LENGTH = 1000;

// The chat's one status line for screen readers; the log itself stays quiet.
export const ASKING_STATUS = "Looking through your documents…";
export const LOADED_STATUS = "Conversation loaded.";
export const NO_ANSWER_STATUS = "No answer found in your documents.";
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

export const NOT_AGREED_TEXT = "The answers did not agree on every point. Open the page before you rely on it.";

/**
 * The answer's label in words, with one line saying what it means. An answer that
 * needs review only because the other models did not confirm it (every source
 * checked) says that instead (features 25-27).
 */
export function answerLabelText(
  label: Label,
  answer?: Pick<Answer, "agreement" | "citations">,
): AnswerLabelText {
  if (label === "verified") {
    return {
      title: "Verified",
      explanation: "Every source behind this answer was checked when the document was read.",
    };
  }
  const checked = answer !== undefined && answer.citations.every((citation) => citation.label === "verified");
  if (checked && answer?.agreement === "not_confirmed") {
    return { title: "Needs review", explanation: NOT_AGREED_TEXT };
  }
  return {
    title: "Needs review",
    explanation: "At least one source was not fully checked. Open the page before you rely on it.",
  };
}

/** A catalogue name without its maker's prefix: "Google: Gemini 3.8 Flash" is "Gemini 3.8 Flash". */
export function modelName(name: string): string {
  return name.replace(/^[^:]{1,40}:\s+/, "") || name;
}

/** "A", "A and B", "A, B and C". */
function joinNames(names: readonly string[]): string {
  return names.length < 2 ? (names[0] ?? "") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/**
 * The line under an answer more than one model was asked to draft (features 25-27):
 * who answered and whether they agreed. null for one model, and for answers from before.
 */
export function teamLine(answer: Pick<Answer, "agents" | "agents_asked" | "agreement">): string | null {
  const names = (answer.agents ?? []).map(modelName);
  if ((answer.agents_asked ?? 0) <= 1 || names.length === 0) return null;
  const who =
    names.length < (answer.agents_asked ?? 0)
      ? `Only ${joinNames(names)} answered.`
      : `Answered by ${joinNames(names)}.`;
  if (names.length === 1) return who;
  return `${who} ${answer.agreement === "agreed" ? "They agreed." : NOT_AGREED_TEXT}`;
}

/**
 * What the status line says when an answer arrives: its label and first
 * sentence only. Verified only when the answer itself is verified.
 */
export function answerStatusText(answer: Answer | null): string {
  if (!answer || answer.status === "not_found") return NO_ANSWER_STATUS;
  const verified = answer.status === "answered" && answer.label === "verified";
  const first = answer.sentences[0]?.text.trim();
  return [`Answer ready. ${verified ? "Verified" : "Needs review"}.`, first].filter(Boolean).join(" ");
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

/** What a source card's preview highlights: the cited spots, and a value's label (PRD 3 H4). */
export function citationHighlights(citation: Pick<Citation, "regions" | "label_regions">): Region[] {
  return [...citation.regions, ...(citation.label_regions ?? [])];
}

/**
 * The lines saying how the question was read. A follow-up rewritten as a
 * standalone question shows "Searching for: …" (PRD 3 H1); "Understood as"
 * then compares against the rewrite, so it shows only when normalizing changed
 * it again. Both are plain text.
 */
export function questionReading(
  question: string | null,
  answer: Pick<Answer, "normalized_question" | "rewritten_question">,
): { searchingFor: string | null; understoodAs: string | null } {
  const searchingFor = answer.rewritten_question?.trim() || null;
  const asked = searchingFor ?? question;
  return {
    searchingFor,
    understoodAs: asked !== null ? understoodAs(asked, answer.normalized_question) : null,
  };
}

export const DELETED_SOURCE_TEXT = "From a deleted document";
export const OLDER_VERSION_TEXT = "From an older version";

/**
 * What became of a cited document, for its source card. "gone": it is in
 * Trash or deleted, so the server sent no snippet and no spot, and the card
 * shows no link and no preview. "older": a newer version is in use now; the
 * card still links to the version that was cited. A missing state is "live".
 */
export function citationSource(citation: Pick<Citation, "source_state">): "live" | "older" | "gone" {
  switch (citation.source_state) {
    case "in_trash":
    case "deleted":
      return "gone";
    case "older_version":
      return "older";
    default:
      return "live";
  }
}

/** "Open page": the document screen at the cited page, with the spot highlighted. */
export function citationLink(citation: Citation): string {
  return pageLink(citation.document_id, citation.page_no, citationRegion(citation));
}

/**
 * "See scanned text": the cited page with its Scanned text panel open and the
 * cited lines marked. null for a gone source (its text is not shown any more)
 * and for a sheet's cell, whose grid already is the reading.
 */
export function scannedLink(citation: Citation): string | null {
  const region = citationRegion(citation);
  if (citationSource(citation) === "gone" || !region || region.kind === "cell") return null;
  return pageLink(citation.document_id, citation.page_no, region, { scanned: true });
}

/**
 * With a mouse or trackpad (a fine pointer), Enter sends and Shift+Enter makes
 * a new line. On a touch screen (`coarse`), Enter always makes a new line and
 * the Send button sends, as phone keyboards expect. Typing with an input
 * method never sends.
 */
export function sendsOnKey(
  event: { key: string; shiftKey: boolean; isComposing: boolean },
  coarse = false,
): boolean {
  if (coarse) return false;
  return event.key === "Enter" && !event.shiftKey && !event.isComposing;
}

/** The question to send, or null when there is nothing to send. */
export function cleanQuestion(text: string): string | null {
  const question = text.trim();
  if (!question) return null;
  return question.slice(0, MAX_QUESTION_LENGTH);
}

/**
 * The box empties as soon as a question is sent. If sending fails, the sent
 * text comes back, but only into an empty box: anything typed meanwhile is
 * never overwritten.
 */
export function restoreAfterFailure(current: string, sent: string): string {
  return current === "" ? sent : current;
}

/** A down vote's reason: optional, trimmed, at most 500 characters. */
export function cleanReason(text: string): string | undefined {
  const reason = text.trim();
  return reason ? reason.slice(0, MAX_REASON_LENGTH) : undefined;
}

/** PRD 5 F6, U7: said on a source card whose cited text holds an unsure or illegible word. */
export const HARD_TO_READ_TEXT = "Part of this text was hard to read.";

/**
 * F6's note (U7), as the server says it: under a not-found answer, "Not found in the
 * readable text. 2 pages couldn't be read: ...", with a link to each page it names and
 * how many more there are. null when there is none (and for answers from before it).
 */
export function readingNote(
  answer: Pick<Answer, "reading_note" | "unreadable_pages" | "unreadable_page_count">,
): { text: string; pages: { href: string; text: string }[]; more: number } | null {
  if (!answer.reading_note) return null;
  const pages = (answer.unreadable_pages ?? []).map((page) => ({
    href: pageLink(page.document_id, page.page_no),
    text: `${page.document_name} page ${page.page_no}`,
  }));
  const more = Math.max(0, (answer.unreadable_page_count ?? pages.length) - pages.length);
  return { text: answer.reading_note, pages, more };
}
