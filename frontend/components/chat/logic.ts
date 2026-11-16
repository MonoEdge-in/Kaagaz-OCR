// The chat screen's layout decisions, kept out of the components so they are
// unit tested in node (tests/chat-logic.test.ts): how the conversation list is
// grouped by day, what time each item shows, when the thread follows a new
// answer, what a source card says about its page, and what a failed question
// tells the person to do.

import { flushDrafts, readDraft, saveDraft } from "@/lib/drafts";
import { errorWords } from "@/lib/errorWords";
import type { Draft } from "@/lib/drafts";
import { formatDate, typeLabel } from "@/lib/format";
import { pageLink } from "@/lib/regions";
import type {
  AnswerChoice,
  ChatOptions,
  ChatResponse,
  Citation,
  ConversationSummary,
  DetectedType,
  DocType,
  DocumentSummary,
  Message,
  Region,
  ThinkingLevel,
} from "@/lib/types";
import { citationRegion, linkWords, MAX_QUESTION_LENGTH, restoreAfterFailure } from "./answer";

// --- Grouping the conversation list by day ------------------------------------

const DAY_MS = 24 * 60 * 60 * 1000;
// Asia/Kolkata is UTC+5:30 all year (India has no daylight saving time), so the
// India calendar day of an instant is plain arithmetic, whatever the browser's zone.
const INDIA_OFFSET_MS = (5 * 60 + 30) * 60 * 1000;

function indiaDay(ms: number): number {
  return Math.floor((ms + INDIA_OFFSET_MS) / DAY_MS);
}

export type ConversationGroupKey = "today" | "yesterday" | "earlier";

export interface ConversationGroup {
  key: ConversationGroupKey;
  title: string;
  items: ConversationSummary[];
}

const GROUP_ORDER: readonly ConversationGroupKey[] = ["today", "yesterday", "earlier"];

const GROUP_TITLES: Record<ConversationGroupKey, string> = {
  today: "Today",
  yesterday: "Yesterday",
  earlier: "Earlier",
};

/**
 * Which group a conversation belongs to, by the India day it was created on.
 * A time slightly ahead of `now` (a server clock running fast) counts as today;
 * an unreadable time goes under Earlier.
 */
export function conversationGroup(createdAt: string, now: number): ConversationGroupKey {
  const created = Date.parse(createdAt);
  if (Number.isNaN(created)) return "earlier";
  const daysAgo = indiaDay(now) - indiaDay(created);
  if (daysAgo <= 0) return "today";
  if (daysAgo === 1) return "yesterday";
  return "earlier";
}

/** Today, Yesterday and Earlier, in that order, leaving out empty groups. Order inside a group is kept. */
export function groupConversations(
  items: readonly ConversationSummary[],
  now: number,
): ConversationGroup[] {
  const buckets: Record<ConversationGroupKey, ConversationSummary[]> = {
    today: [],
    yesterday: [],
    earlier: [],
  };
  for (const item of items) buckets[conversationGroup(item.created_at, now)].push(item);
  return GROUP_ORDER.filter((key) => buckets[key].length > 0).map((key) => ({
    key,
    title: GROUP_TITLES[key],
    items: buckets[key],
  }));
}

const timeFormat = new Intl.DateTimeFormat("en-IN", {
  hour: "numeric",
  minute: "2-digit",
  timeZone: "Asia/Kolkata",
});

/** Beside a conversation asked on WhatsApp (#603): "From WhatsApp"; null for the browser's. */
export function conversationOrigin(item: ConversationSummary): string | null {
  return item.channel === "whatsapp" ? "From WhatsApp" : null;
}

/** Beside each item: the time for Today and Yesterday ("2:30 pm"), the date for Earlier. */
export function conversationTime(createdAt: string, group: ConversationGroupKey): string {
  const created = Date.parse(createdAt);
  if (Number.isNaN(created)) return "";
  return group === "earlier" ? formatDate(createdAt) : timeFormat.format(created);
}

// --- Following new answers in the thread ----------------------------------------

/** How close to the end still counts as "reading the latest", in CSS pixels. */
export const NEAR_BOTTOM_PX = 120;

export interface ScrollMetrics {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

/** True when the person is at (or close to) the end of the thread. */
export function isNearBottom(metrics: ScrollMetrics, threshold: number = NEAR_BOTTOM_PX): boolean {
  return metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight <= threshold;
}

/**
 * Where to scroll so a new answer shows from its first line: its top less a
 * small gap, but never past the end. A short answer therefore just shows the end.
 */
export function answerScrollTop(
  metrics: Pick<ScrollMetrics, "scrollHeight" | "clientHeight">,
  answerTop: number,
  gap = 16,
): number {
  const end = Math.max(0, metrics.scrollHeight - metrics.clientHeight);
  return Math.max(0, Math.min(end, answerTop - gap));
}

/** After this long, the pending answer says a long register takes a little longer. */
export const STILL_LOOKING_AFTER_MS = 10_000;

// --- Source cards ---------------------------------------------------------------

// Spreadsheets and CSV files have sheets, not pages; the backend numbers them as pages. A
// text file (#522) has pages of about 3,000 characters.
const SHEET_TYPES: ReadonlySet<DetectedType> = new Set<DetectedType>(["zip", "ole2", "xlsx", "xls", "csv"]);

function placeWord(citation: Pick<Citation, "detected_type">): "Page" | "Sheet" {
  return SHEET_TYPES.has(citation.detected_type) ? "Sheet" : "Page";
}

type Place = Pick<Citation, "detected_type" | "page_no" | "page_from" | "page_to">;

/**
 * "Page 3", or "Pages 15 to 16" for a source that runs onto later pages (PRD 3
 * H4); "Sheet 3" for a spreadsheet. An answer saved before PRD 3 has no page
 * range, so it shows its one page.
 */
export function sourcePlace(citation: Place): string {
  const from = citation.page_from ?? citation.page_no;
  const to = citation.page_to ?? from;
  const word = placeWord(citation);
  return to > from ? `${word}s ${from} to ${to}` : `${word} ${from}`;
}

/** The meta line under a source's document name: "Page 3 · PDF". */
export function sourceMeta(citation: Place): string {
  return `${sourcePlace(citation)} · ${typeLabel(citation.detected_type)}`;
}

/** "Label on page 15, value on page 16", when a value's label was found on another page; else null (H4). */
export function labelPlace(
  citation: Pick<Citation, "detected_type" | "page_no" | "label_page_no">,
): string | null {
  const label = citation.label_page_no;
  if (label === null || label === undefined || label === citation.page_no) return null;
  const word = placeWord(citation).toLowerCase();
  return `Label on ${word} ${label}, value on ${word} ${citation.page_no}`;
}

export interface PageJump {
  text: string;
  href: string;
}

/**
 * The chips beside a source card's main link (PRD 3 H4): one for each later
 * page its spots run onto ("Page 16"), then one for a value's label on another
 * page ("Label, page 15"). Each opens that page with its spot highlighted, or
 * without one when the spot is gone, and the asker's words (`terms`) lit, as
 * citationLink does (package H). O(regions).
 */
export function pageJumps(citation: Citation, terms: readonly string[] = []): PageJump[] {
  const word = placeWord(citation);
  const words = linkWords(...terms);
  const jump = (text: string, page_no: number, regions: Region[]): PageJump => ({
    text,
    href: pageLink(citation.document_id, page_no, citationRegion({ regions, page_no }), { words }),
  });
  const pages = new Set(citation.regions.map((region) => region.page));
  pages.delete(citation.page_no);
  const jumps = [...pages]
    .sort((a, b) => a - b)
    .map((page) => jump(`${word} ${page}`, page, citation.regions));
  const label = citation.label_page_no;
  if (typeof label === "number" && labelPlace(citation) !== null) {
    jumps.push(jump(`Label, ${word.toLowerCase()} ${label}`, label, citation.label_regions ?? []));
  }
  return jumps;
}

/**
 * The link at the foot of a source card: "Open page 3", the same for every source.
 * No Needs review in Ask (Tushar, 16 Oct 2026): checking values is the Review screen's.
 */
export function openText(citation: Pick<Citation, "detected_type" | "page_no">): string {
  return `Open ${placeWord(citation).toLowerCase()} ${citation.page_no}`;
}

// --- Sending a question ---------------------------------------------------------

// The server's catch-all words ("Something went wrong.", "... on our side."): never shown (B14).
const GENERIC_MESSAGE = /^something went wrong\b/i;

/**
 * What a failed question says, from the error's HTTP status and message (null
 * when the server could not be reached). The typed question always stays in the box.
 */
export function askErrorText(error: { status: number; message: string; code?: string } | null): string {
  // #535: a streamed answer not found in its conversation yet: its own honest words, before its 503.
  if (error?.code === "answer_pending") return error.message;
  if (error === null || error.status === 0) {
    return "Could not reach Kaagaz. Check your internet connection, then try again.";
  }
  if (error.status === 429) return "Too many questions at once. Wait a minute, then try again.";
  if (error.status === 503) return unfinishedText(""); // Ask hotfix (15 Nov 2026): never "busy"
  const message = error.message.trim();
  if (error.status >= 400 && error.status < 500 && message && !GENERIC_MESSAGE.test(message)) {
    return message;
  }
  return "Could not get an answer right now. Try again in a minute.";
}

/** Ask hotfix (production, 15 Nov 2026): how long a question that could not be finished waits
 * before its one automatic retry, with "Looking through your documents…" still on screen. */
export const ASK_RETRY_MS = 2000;

/** A failure Ask retries once, then shows as `unfinishedText` in the answer's place: a 503 (the
 * service could not finish it), but never one whose answer is still being saved (`answer_pending`). */
export function isUnfinished(error: unknown): boolean {
  const failed = error as { status?: unknown; code?: unknown } | null;
  return failed?.status === 503 && failed.code !== "answer_pending";
}

/** What Ask says in the answer's place when it could not finish a question, in normal colours with
 * Try again beside it: never "busy", "error" or "server". The first name only, as Home's greeting. */
export function unfinishedText(displayName: string): string {
  const first = displayName.trim().split(/\s+/)[0];
  return first
    ? `Sorry, ${first}, I couldn't finish that just now. Please try again in a moment.`
    : "Sorry, I couldn't finish that just now. Please try again in a moment.";
}

/** B1: a question stopped after `started`, in the thread under the id the server saves it with,
 * until its saved answer takes its place (withSavedAnswer). */
export function stoppedQuestion(id: string, text: string): Message {
  return { id, role: "user", text, created_at: new Date().toISOString(), answer: null, feedback: null };
}

/** The thread with a stopped question's saved answer: in the question's place (so a question asked
 * since stays after it), or at the end when the question is not there; unchanged if already in. */
export function withSavedAnswer(messages: readonly Message[], saved: ChatResponse): Message[] {
  if (messages.some((message) => message.id === saved.answer.id)) return [...messages];
  const at = messages.findIndex((message) => message.id === saved.question.id);
  if (at < 0) return [...messages, saved.question, saved.answer];
  return [...messages.slice(0, at), saved.question, saved.answer, ...messages.slice(at + 1)];
}

/** Why a conversation did not load, and what to do (issue 468): Try again sits beside it. */
export const conversationLoadText = (caught: unknown): string => `Could not load this conversation. ${errorWords(caught)}`;

// --- The question box's draft (issue 468) -------------------------------------
// Kept in this tab for the same person (lib/drafts.ts): the words typed, and the question
// still waiting for its answer. Back on Ask it goes back in the box, never sent by itself.

export const ASK_DRAFT = "ask";

/** What Ask keeps, or null for nothing. */
export function askDraft(typed: string, waiting: string | null): Draft | null {
  const fields: Draft = {};
  if (typed.trim()) fields.typed = typed;
  if (waiting?.trim()) fields.waiting = waiting;
  return Object.keys(fields).length > 0 ? fields : null;
}

/** The words a kept draft puts back in the box: the question that got no answer, else the typing. */
export function askDraftText(draft: Draft | null): string | null {
  const words = (value: unknown) => (typeof value === "string" && value.trim() ? value : null);
  return words(draft?.waiting) ?? words(draft?.typed);
}

/**
 * A question settled after its chat screen was gone: forget it as waiting. Answered, it is
 * done; not, it stays as typing (unless something else was typed), as the box would keep it.
 */
export function settleWaitingDraft(userId: string, question: string, answered: boolean): void {
  flushDrafts();
  const kept = readDraft(userId, ASK_DRAFT);
  if (kept?.waiting !== question) return;
  const typed = typeof kept.typed === "string" ? kept.typed : "";
  saveDraft(userId, ASK_DRAFT, askDraft(answered ? typed : restoreAfterFailure(typed, question), null));
}

// --- The open conversation lives in the URL (/chat?c=<id>) --------------------
// so a reload, or a link, brings the same conversation back. Only an id shaped
// like a UUID is read; the server still decides whether it is this person's.

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The conversation id in a location's query string, or null. */
export function conversationFromSearch(search: string): string | null {
  const id = new URLSearchParams(search).get("c");
  return id !== null && UUID.test(id) ? id.toLowerCase() : null;
}

/** The chat address for a conversation, or the empty chat. */
export function chatHref(conversationId: string | null): string {
  return conversationId ? `/chat?c=${encodeURIComponent(conversationId)}` : "/chat";
}

// "Ask about this document" opens a new chat scoped to one document
// (/chat?document=<id>, PRD 3 H2). A conversation named in the same address wins.

/** The document a new chat is scoped to, or null. */
export function documentFromSearch(search: string): string | null {
  if (conversationFromSearch(search) !== null) return null;
  const id = new URLSearchParams(search).get("document");
  return id !== null && UUID.test(id) ? id.toLowerCase() : null;
}

// Home's Ask box and question suggestions open chat with an already-submitted question.

/** The question an address brings (trimmed, at most MAX_QUESTION_LENGTH characters), or null. */
export function questionFromSearch(search: string): string | null {
  const question = (new URLSearchParams(search).get("q") ?? "").trim();
  return question ? question.slice(0, MAX_QUESTION_LENGTH) : null;
}

// One ?q= contract: /chat?q= puts the words in the box (questionFromSearch); send=1 also sends them.

/** The chat address that submits a question sent from Home. */
export function askHref(question: string): string {
  return `/chat?q=${encodeURIComponent(question)}&send=1`;
}

/** The chat address with these words in the question box, never sent (#468: a search that found nothing). */
export function askWithWordsHref(q: string): string {
  return `/chat?q=${encodeURIComponent(q)}`;
}

/** The chat address that asks about one document. */
export function askAboutHref(documentId: string): string {
  return `/chat?document=${encodeURIComponent(documentId)}`;
}

// --- Answer settings (features 25-27) ------------------------------------------
// How hard Ask works on an answer, picked in the composer from what the organisation allows
// (GET /chat/options): Swift or Precision. No model's name, price or cost ever. Kept in this
// browser (loadPick, savePick), so tabs and reloads keep it.

// Changed on purpose (SoW 19 Oct 2026): Ask calls Medium "Swift" and High "Precision", and does
// not offer Low (the admin's AI settings still name all three).
export const LEVEL_NAMES: Record<ThinkingLevel, string> = { low: "Low", medium: "Swift", high: "Precision" };

/** The levels Ask offers: the organisation's, without Low. */
export const askLevels = (options: ChatOptions): ThinkingLevel[] => options.levels.filter((level) => level !== "low");

// Changed on purpose (Vrushit UAT, 25 Oct 2026): the engine alone. The Ask bar's quality picker
// (a model by "High", "Extra high"...) is gone; Precision brings the stronger model itself.
export interface AnswerPick {
  level: ThinkingLevel;
}

/** Where Ask starts: Swift whenever the organisation allows it (Tushar, 25 Oct 2026), else its
 * own level. */
export function defaultPick(options: ChatOptions): AnswerPick {
  return { level: options.levels.includes("medium") ? "medium" : options.default_level };
}

/** A pick saved earlier, if the organisation still allows its level; else null. A model kept
 * by an older screen is ignored. */
export function usablePick(options: ChatOptions, pick: unknown): AnswerPick | null {
  if (typeof pick !== "object" || pick === null) return null;
  const { level } = pick as Partial<AnswerPick>;
  if (typeof level !== "string" || !askLevels(options).includes(level as ThinkingLevel)) return null;
  return { level: level as ThinkingLevel };
}

const PICK_KEY = "kaagaz.ask.pick";

/** The pick this browser kept (Tushar, 25 Oct 2026: Swift or Precision stays chosen across tabs
 * and reloads), or null; storage refused (private mode) reads as nothing kept. */
export function loadPick(): unknown {
  try {
    const raw = window.localStorage.getItem(PICK_KEY);
    return raw === null ? null : JSON.parse(raw);
  } catch {
    return null;
  }
}

/** Keep the pick in this browser; storage refused is ignored (the pick still holds in this tab). */
export function savePick(pick: AnswerPick): void {
  try {
    window.localStorage.setItem(PICK_KEY, JSON.stringify({ level: pick.level }));
  } catch {
    // shortcut: private mode keeps the pick in this tab only, which is today's behaviour
  }
}

/** The composer's engine button (Swift or Precision) shows only when there are two to choose. */
export function hasChoices(options: ChatOptions | null): options is ChatOptions {
  return options !== null && askLevels(options).length > 1;
}

/** The composer's chip: the engine ("Swift"). */
export function pickSummary(options: ChatOptions, pick: AnswerPick): string {
  return LEVEL_NAMES[pick.level];
}

/** What POST /chat sends. Swift (or any level but High): the organisation's default model at
 * that level, nothing when it is the organisation's own level. Precision always sends High, so
 * the server answers on the strongest model it allows that takes High (Vrushit UAT, 25 Oct
 * 2026: chat.py _choice), reading more of the documents (no Swift input cap). */
export function choiceBody(options: ChatOptions | null, pick: AnswerPick | null): AnswerChoice | undefined {
  if (options === null || pick === null) return undefined;
  // The organisation's own level, not Ask's starting one: Swift over a Low default is sent.
  if (pick.level !== "high" && pick.level === options.default_level) return undefined;
  return { thinking: pick.level };
}

// --- UI audit, 16 Oct 2026 (A1, A5) ---------------------------------------------

/** A1: the conversations whose title has the search's words, in the list's order. */
export function filterConversations(items: readonly ConversationSummary[], search: string): ConversationSummary[] {
  const words = search.toLowerCase().split(/\s+/).filter(Boolean);
  return items.filter((item) => words.every((word) => item.title.toLowerCase().includes(word)));
}

const STARTERS: Partial<Record<DocType, string>> = {
  invoice: "What is the total amount on this invoice?",
  receipt: "How much was paid, and to whom?",
  bank_statement: "What is the closing balance?",
  land_record: "Who is the owner named in this record?",
  contract: "Who are the parties, and when does it end?",
  register: "Which entry has the largest amount?",
};
// Any document: the first not yet suggested, so no question shows twice (17 Oct 2026).
const STARTERS_ANY = [
  "What is this document about?",
  "Who are the parties named in it?",
  "What amounts does it mention?",
  "When is it dated?",
];
export const MAX_STARTERS = 3;

export interface Starter {
  documentId: string;
  documentName: string;
  question: string;
}

/**
 * A5: up to three example questions about the newest ready documents, one each, by the
 * document's type. Each is asked about its own document only. Plain, fixed questions: no
 * AI call makes them. Never the same question twice (Ask screen polish, 17 Oct 2026): a
 * type's question already used, or a document with no type, takes the next unused one.
 */
export function starterQuestions(documents: readonly DocumentSummary[]): Starter[] {
  const used = new Set<string>();
  const starters: Starter[] = [];
  for (const doc of documents) {
    if (starters.length === MAX_STARTERS) break;
    if (doc.status !== "ready" || doc.deleted_at !== null) continue;
    const typed = doc.doc_type ? STARTERS[doc.doc_type] : undefined;
    const question = [typed, ...STARTERS_ANY].find((text) => text !== undefined && !used.has(text));
    if (!question) continue;
    used.add(question);
    starters.push({ documentId: doc.id, documentName: doc.display_name, question });
  }
  return starters;
}
