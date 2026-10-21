// The chat screen's layout decisions, kept out of the components so they are
// unit tested in node (tests/chat-logic.test.ts): how the conversation list is
// grouped by day, what time each item shows, when the thread follows a new
// answer, what a source card says about its page, and what a failed question
// tells the person to do.

import { formatDate, typeLabel } from "@/lib/format";
import { pageLink } from "@/lib/regions";
import type {
  AnswerChoice,
  ChatOptions,
  Citation,
  ConversationSummary,
  DetectedType,
  DocType,
  DocumentSummary,
  Region,
  ThinkingLevel,
} from "@/lib/types";
import { citationRegion } from "./answer";

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

// Spreadsheets and CSV files have sheets, not pages; the backend numbers them as pages.
const SHEET_TYPES: ReadonlySet<DetectedType> = new Set<DetectedType>(["zip", "ole2", "text"]);

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
 * without one when the spot is gone. O(regions).
 */
export function pageJumps(citation: Citation): PageJump[] {
  const word = placeWord(citation);
  const jump = (text: string, page_no: number, regions: Region[]): PageJump => ({
    text,
    href: pageLink(citation.document_id, page_no, citationRegion({ regions, page_no })),
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

const GENERIC_MESSAGE = "Something went wrong.";

/**
 * What a failed question says, from the error's HTTP status and message (null
 * when the server could not be reached). The typed question always stays in the box.
 */
export function askErrorText(error: { status: number; message: string } | null): string {
  if (error === null || error.status === 0) {
    return "Could not reach Kaagaz. Check your internet connection, then try again.";
  }
  if (error.status === 429) return "Too many questions at once. Wait a minute, then try again.";
  if (error.status === 503) return "Kaagaz is busy right now. Try again in a minute."; // the AI service is down
  const message = error.message.trim();
  if (error.status >= 400 && error.status < 500 && message && message !== GENERIC_MESSAGE) {
    return message;
  }
  return "Could not get an answer right now. Try again in a minute.";
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

// Home's Ask box opens chat with its question (/chat?q=...): it waits in the question
// box for Send, so nothing is asked, and no AI call is made, until the person sends it.

/** The question an address brings (trimmed, at most 1,000 characters), or null. */
export function questionFromSearch(search: string): string | null {
  const question = (new URLSearchParams(search).get("q") ?? "").trim();
  return question ? question.slice(0, 1000) : null;
}

/** The chat address that brings a question to the box. */
export function askHref(question: string): string {
  return `/chat?q=${encodeURIComponent(question)}`;
}

/** The chat address that asks about one document. */
export function askAboutHref(documentId: string): string {
  return `/chat?document=${encodeURIComponent(documentId)}`;
}

// --- Answer settings (features 25-27) ------------------------------------------
// Which models answer and how hard they think, picked by name in the composer from
// what the organisation allows (GET /chat/options). No price or cost ever: names
// and levels only. Kept in React state only; a reload starts from the defaults.

export const LEVEL_NAMES: Record<ThinkingLevel, string> = { low: "Low", medium: "Medium", high: "High" };

export interface AnswerPick {
  agents: string[]; // model ids, in the order picked; [] for the organisation's default
  level: ThinkingLevel;
}

/** The organisation's defaults: its default model (none listed: the server's), its level. */
export function defaultPick(options: ChatOptions): AnswerPick {
  const main = options.agents.find((agent) => agent.default);
  return { agents: main ? [main.id] : [], level: options.default_level };
}

/** Answer settings are shown only when there is something to choose. */
export function hasChoices(options: ChatOptions | null): options is ChatOptions {
  return options !== null && (options.agents.length > 1 || options.levels.length > 1);
}

/** One model answers in Ask (Tushar, 16 Oct 2026): picking one replaces the last; every
 * level stays open to it. */
export function chooseAgent(pick: AnswerPick, id: string): AnswerPick {
  return { ...pick, agents: [id] };
}

/** The chip: "Standard · High". Changed on purpose (spec 17 Oct 2026): org users never see a
 * model's name, so the model is its tier (shown only when there is more than one), then the level. */
export function pickSummary(options: ChatOptions, pick: AnswerPick): string {
  const index = options.agents.findIndex((agent) => agent.id === pick.agents[0]);
  const tier = options.agents.length > 1 && index >= 0 ? agentTier(options, index) : "Thinking";
  return `${tier} · ${LEVEL_NAMES[pick.level]}`;
}

/** A model's neutral name for org users: the default is "Standard", the others "Careful" (then "Careful 2"…). */
export function agentTier(options: ChatOptions, index: number): string {
  const agent = options.agents[index];
  if (!agent || agent.default) return "Standard";
  const place = options.agents.filter((each, at) => !each.default && at <= index).length;
  return place === 1 ? "Careful" : `Careful ${place}`;
}

/** What POST /chat sends: nothing when the pick is the organisation's default. */
export function choiceBody(options: ChatOptions | null, pick: AnswerPick | null): AnswerChoice | undefined {
  if (options === null || pick === null) return undefined;
  const usual = defaultPick(options);
  if (pick.level === usual.level && pick.agents.join(",") === usual.agents.join(",")) return undefined;
  return { ...(pick.agents.length > 0 ? { agents: pick.agents.slice(0, 1) } : {}), thinking: pick.level };
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
