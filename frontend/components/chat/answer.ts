// The chat screen's small decisions, kept out of the components so they are
// unit tested in node (tests/chat-*.test.ts): which source card a citation
// number points at, when to show how the
// question was understood, what a source card may still show of its
// document, and what the question box sends or gets back.

import { pageLink } from "@/lib/regions";
import type {
  Answer,
  AnswerSentence,
  Citation,
  ListedDocument,
  Mentions,
  Message,
  ReasonCode,
  Region,
  RelatedDocument,
  SourcePin,
} from "@/lib/types";

// PRD 6 decision 9 (24 Oct 2026): the server's words exactly (backend/kaagaz/answering/voice.py,
// a test reads both); no "sorry", no "couldn't". The closest documents and next steps follow.
// Changed on purpose (#410, Vrushit's UAT): nothing decides "every part"; the closest documents
// hold some of the question's words, and the words no document has are named (notFoundText).
export const NOT_FOUND_SOME_TEXT = "These documents match some of your words.";
export const NOT_FOUND_CLOSEST_TEXT = "No answer to the whole question was found. These documents match some of your words.";
export const NOT_FOUND_TEXT = "Nothing in your documents matches this yet.";
// An "unchecked" answer (a figure only in values not confirmed yet): no number, no
// Review wording (Tushar, 16 Oct 2026); the documents follow it.
export const UNCHECKED_TEXT = "This figure is in these documents. Open them to see it.";
export const MAX_QUESTION_LENGTH = 1000;

// The chat's one status line for screen readers; the log itself stays quiet.
export const ASKING_STATUS = "Looking through your documents…";
/** Stop was pressed (24 Oct 2026): said once. #535: Stop only stops the display; the server
 * finishes the answer and saves it in the conversation (Tushar, 16 Oct 2026). */
export const STOPPED_STATUS = "Stopped. The answer will be saved in this conversation.";
export const LOADED_STATUS = "Conversation loaded.";
export const NO_ANSWER_STATUS = "No answer found in your documents.";
export const MAX_REASON_LENGTH = 500;
// PRD 6 F1: what went wrong on Not useful, in the server's order (db/chat.py REASONS).
export const REASONS: readonly { code: ReasonCode; label: string }[] = [
  { code: "wrong_documents", label: "Wrong documents" },
  { code: "document_missing", label: "A document is missing" },
  { code: "wrong_figure", label: "Wrong figure" },
  { code: "did_not_understand", label: "Didn't understand me" },
  { code: "other", label: "Other" },
];
// Decision 7 (24 Oct 2026): said above Send, so only feedback given after it reaches the admin.
export const FEEDBACK_NOTICE = "Your company admin can see this answer, your question, your reason and your name.";

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

/** A catalogue name without its maker's prefix: "Google: Gemini 3.8 Flash" is "Gemini 3.8 Flash". */
export function modelName(name: string): string {
  return name.replace(/^[^:]{1,40}:\s+/, "") || name;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/**
 * What the status line says when an answer arrives: its first sentence only, never the
 * opening, and for a v2 answer (PRD 6 A) how many documents it lists: "Answer ready. The
 * total is 1,234. 2 documents."; a v2 not-found: "Nothing matches exactly. 3 closest
 * documents." No label words. Answers from before v2 are said as they were.
 */
export function answerStatusText(answer: Answer | null): string {
  if (answer?.version === 2 && answer.status !== "unchecked") {
    const { documents, more } = listedDocuments(answer);
    const count = documents.length + more;
    if (answer.status === "not_found") {
      return count > 0 ? `Nothing matches exactly. ${plural(count, "closest document", "closest documents")}.` : NOT_FOUND_TEXT;
    }
    const said = [answer.sentences[0]?.text.trim(), count > 0 ? `${plural(count, "document", "documents")}.` : ""];
    return ["Answer ready.", ...said].filter(Boolean).join(" ");
  }
  if (!answer || answer.status === "not_found") return NO_ANSWER_STATUS;
  if (answer.status === "clarify") return answer.clarify?.question ?? NO_ANSWER_STATUS; // PRD 6 B2
  const first = answer.status === "unchecked" ? UNCHECKED_TEXT : answer.sentences[0]?.text.trim();
  return ["Answer ready.", first].filter(Boolean).join(" ");
}

/** The not-found line (decision 9): the closest documents' when it lists any. #410: the
 * question's words no document searched has (`missing_words`) are named first, as voice.py's
 * soft_line says them. */
export function notFoundText(closest: number, missing: readonly string[] = []): string {
  if (missing.length === 0) return closest > 0 ? NOT_FOUND_CLOSEST_TEXT : NOT_FOUND_TEXT;
  const quoted = missing.map((word) => `“${word}”`);
  const words =
    quoted.length === 1 ? `word ${quoted[0]}` : `words ${quoted.slice(0, -1).join(", ")} or ${quoted[quoted.length - 1]}`;
  const line = `No document searched has the ${words}.`;
  return closest > 0 ? `${line} ${NOT_FOUND_SOME_TEXT}` : line;
}

/** The DOM id of an answer card: focus moves to a new one (PRD 6 A). */
export function answerCardId(messageId: string): string {
  return `answer-${messageId}`;
}

export const MAX_LISTED_DOCUMENTS = 10;

/** PRD 6 A: the documents listed under an answer, each with its sources, at most 10; `more`
 * counts the other documents its sources come from (they are in Sources). O(c). */
export function listedDocuments(answer: Pick<Answer, "documents" | "citations">): {
  documents: (ListedDocument & { sources: Citation[] })[];
  more: number;
} {
  const cards = citationMap(answer);
  const listed = (answer.documents ?? []).slice(0, MAX_LISTED_DOCUMENTS);
  const shown = new Set(listed.map((doc) => doc.document_id));
  const others = new Set(answer.citations.map((c) => c.document_id).filter((id) => !shown.has(id)));
  return {
    documents: listed.map((doc) => ({
      ...doc,
      sources: doc.citations.flatMap((n) => {
        const card = cards.get(n);
        return card ? [card] : [];
      }),
    })),
    more: others.size,
  };
}

/** The figure a value source prints, as printed ("₹1,234.00" of "total_amount: ₹1,234.00");
 * null for a piece of text or a source that is gone. */
export function figureOf(citation: Citation): string | null {
  if (citation.kind !== "value" || !citation.snippet) return null;
  const at = citation.snippet.indexOf(": ");
  return at >= 0 ? citation.snippet.slice(at + 2) : citation.snippet;
}

const DEVANAGARI_RUN = /([ऀ-ॿ]+(?:[\s/.,-]+[ऀ-ॿ]+)*)/u;

/** `text` in runs, each Devanagari run marked `lang: "mr"` (most documents are Marathi; the
 * build spec asks for mr or hi), so a screen reader reads it in its own voice. O(n). */
export function scriptParts(text: string): { text: string; lang?: "mr" }[] {
  return text
    .split(DEVANAGARI_RUN)
    .filter(Boolean)
    .map((part) => (/[ऀ-ॿ]/u.test(part) ? { text: part, lang: "mr" } : { text: part }));
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

/** "Open page": the document screen at the cited page, with the spot highlighted, and the
 * asker's words (`terms`, matchTerms) lit there too (package H, linkWords). */
export function citationLink(citation: Citation, terms: readonly string[] = []): string {
  return pageLink(citation.document_id, citation.page_no, citationRegion(citation), { words: linkWords(...terms) });
}

/** A document opened at its start (a closest document), the asker's words lit (package H). */
export function documentLink(documentId: string, terms: readonly string[] = []): string {
  const words = linkWords(...terms);
  const base = `/documents/${encodeURIComponent(documentId)}`;
  return words.length > 0 ? `${base}?${new URLSearchParams({ words: words.join(" ") }).toString()}` : base;
}

export const MAX_LINK_WORDS = 8;
export const MAX_LINK_WORD_CHARS = 40;

/** Package H: the words a link into the viewer carries, and the viewer reads back from it: the
 * texts' matchTerms (never a stop word), the first MAX_LINK_WORDS, each at most 40 characters. */
export function linkWords(...texts: (string | null | undefined)[]): string[] {
  return matchTerms(...texts)
    .slice(0, MAX_LINK_WORDS)
    .map((word) => word.slice(0, MAX_LINK_WORD_CHARS));
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

/** A Not useful reason: optional, trimmed, at most 500 characters. */
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
  return { text: pages.length > 0 ? shortNote(answer.reading_note) : answer.reading_note, pages, more };
}

/** The note without the page names it lists, when the pages are shown as links in
 * Sources (Tushar, 16 Oct 2026): "Not found in the readable text. 20 pages to check."
 * A note of another shape is kept whole. O(n). */
export function shortNote(note: string): string {
  const head = /^(.*?\bpages? to check):\s/s.exec(note);
  return head ? `${head[1]}.` : note;
}

// --- Compact, ranked sources (Tushar, 16 Oct 2026) -------------------------------
// No page image in Ask: each source is a row with its name, page and a few lines of
// its text, the question's words marked. The most relevant first (the search's rank),
// and a person may put one on top ("Most relevant"), kept for that answer.

// Words too common to mark (English and the Romanised Hindi or Marathi fillers).
const UNMARKED = new Set(
  (
    "the and for are was were what which who whom whose when where why how many much this that these those" +
    " with from into have has had did does our your their there here any all can show tell give find list" +
    " kya hai hain tha the kitna kitne kitni kaun kahan kab kis kiska kiski kiske aur mein main ahe kiti"
  ).split(" "),
);

// A word: letters, marks and digits, "-" or "/" inside it, and "," or "." between two digits,
// so "1,042", "1,04,200", "7/12" and "INV-1042" stay whole (package H review: never "042").
const TERM = /[\p{L}\p{M}\p{N}]+(?:(?:(?<=\p{N})[,.](?=\p{N})|[/-])[\p{L}\p{M}\p{N}]+)*/gu;

/** The words to mark in a source's text: the question's and how it was read, three
 * letters or more (or digits), each once, longest first. O(n). */
export function matchTerms(...texts: (string | null | undefined)[]): string[] {
  const words = new Set<string>();
  for (const text of texts) {
    for (const bare of (text ?? "").normalize("NFKC").toLowerCase().match(TERM) ?? []) {
      if (bare.length >= 3 && !UNMARKED.has(bare)) words.add(bare);
    }
  }
  return [...words].sort((a, b) => b.length - a.length);
}

const escapeRegExp = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
// Whole words only: "total" marks "Total:", never the "total" inside "Subtotal".
const termPattern = (terms: readonly string[]) =>
  terms.length > 0
    ? new RegExp(`(?<![\\p{L}\\p{M}\\p{N}])(?:${terms.map(escapeRegExp).join("|")})(?![\\p{L}\\p{M}\\p{N}])`, "giu")
    : null;

export interface TextPart {
  text: string;
  mark: boolean;
}

/** `text` in plain and marked parts (any case); plain text only, never markup. O(n * t). */
export function markParts(text: string, terms: readonly string[]): TextPart[] {
  const pattern = termPattern(terms);
  if (!pattern || !text) return text ? [{ text, mark: false }] : [];
  const parts: TextPart[] = [];
  let at = 0;
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > at) parts.push({ text: text.slice(at, start), mark: false });
    parts.push({ text: match[0], mark: true });
    at = start + match[0].length;
  }
  if (at < text.length) parts.push({ text: text.slice(at), mark: false });
  return parts;
}

/** About `max` characters of a source's text around its first matched word, from the
 * start of that line (or a word just before it), "…" where it was cut. The card shows
 * at most four lines of it (Chat.module.css). O(n). */
export function snippetAround(snippet: string, terms: readonly string[], max = 320): string {
  const first = termPattern(terms)?.exec(snippet)?.index ?? 0;
  let start = 0;
  if (first > max / 2) {
    const line = snippet.lastIndexOf("\n", first) + 1;
    start = first - line <= 80 ? line : snippet.lastIndexOf(" ", first - 40) + 1;
  }
  const cut = snippet.slice(start, start + max).trim();
  return `${start > 0 ? "…" : ""}${cut}${start + max < snippet.length ? "…" : ""}`;
}

/** Is this the source put on top? A cited one by its ref when the pick names one. */
export function isPinned(
  pin: SourcePin | null | undefined,
  documentId: string,
  pageNo: number,
  refId?: string,
): boolean {
  if (!pin || pin.document_id !== documentId || pin.page_no !== pageNo) return false;
  return !pin.ref_id || refId === undefined || pin.ref_id === refId;
}

/** The cited sources in the Sources bar's order: by the search's rank (1 first; none
 * after), then as the answer numbers them; the one put on top first. Two sources on the
 * same page (a pick names a page): only the first of them moves. Numbers in the answer's
 * text never change. O(c log c). */
export function rankedSources(citations: readonly Citation[], pin?: SourcePin | null): Citation[] {
  const rank = (c: Citation) => c.rank ?? Infinity;
  const sorted = [...citations].sort((a, b) => rank(a) - rank(b) || a.n - b.n);
  const at = sorted.findIndex((c) => isPinned(pin, c.document_id, c.page_no, c.ref_id));
  return at > 0 ? [sorted[at]!, ...sorted.slice(0, at), ...sorted.slice(at + 1)] : sorted;
}

/** "Also in", the one put on top first, else in the search's order. */
export function rankedRelated(related: readonly RelatedDocument[], pin?: SourcePin | null): RelatedDocument[] {
  const pinned = related.filter((doc) => isPinned(pin, doc.document_id, doc.page_no));
  return [...pinned, ...related.filter((doc) => !pinned.includes(doc))];
}

/** The Sources bar's summary after "Sources": "4 · Also in 3 documents · 2 pages to check
 * · Mentioned in 12 more documents". */
export function sourcesCount(cited: number, related: number, pagesToCheck = 0, mentioned = 0): string {
  return [
    cited > 0 ? String(cited) : "",
    related > 0 ? `Also in ${related} ${related === 1 ? "document" : "documents"}` : "",
    pagesToCheck > 0 ? `${pagesToCheck} ${pagesToCheck === 1 ? "page" : "pages"} to check` : "",
    mentioned > 0 ? `Mentioned in ${mentioned} more ${mentioned === 1 ? "document" : "documents"}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

/** "Also mentions" (Tushar, 17 Oct 2026): the answer's list, or null when it has none. */
export function mentionsOf(answer: Pick<Answer, "mentions">): Mentions | null {
  const mentions = answer.mentions;
  return mentions && mentions.documents.length > 0 ? mentions : null;
}

/** "Page 3", "Pages 2, 5", or "Pages 2, 5, 7, 9, 11 and 4 more" for a mentioned document. */
export function mentionPages(pages: readonly number[], page: number, shown = 5): string {
  const all = pages.length > 0 ? pages : [page];
  if (all.length === 1) return `Page ${all[0]}`;
  const more = all.length - shown;
  return `Pages ${all.slice(0, shown).join(", ")}${more > 0 ? ` and ${more} more` : ""}`;
}
