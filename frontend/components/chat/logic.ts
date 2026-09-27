// The chat screen's layout decisions, kept out of the components so they are
// unit tested in node (tests/chat-logic.test.ts): how the conversation list is
// grouped by day, what time each item shows, when the thread follows a new
// answer, what a source card says about its page, and what a failed question
// tells the person to do.

import { formatDate, typeLabel } from "@/lib/format";
import type { Citation, ConversationSummary, DetectedType } from "@/lib/types";

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

/** "Page 3", or "Sheet 3" for a spreadsheet. */
export function sourcePlace(citation: Pick<Citation, "detected_type" | "page_no">): string {
  return `${placeWord(citation)} ${citation.page_no}`;
}

/** The meta line under a source's document name: "Page 3 · PDF". */
export function sourceMeta(citation: Pick<Citation, "detected_type" | "page_no">): string {
  return `${sourcePlace(citation)} · ${typeLabel(citation.detected_type)}`;
}

export interface SourceAction {
  text: string; // the link text
  check: boolean; // true: shown as an outlined button asking the person to check
  reason: string | null; // why to check, for Needs review
}

/**
 * The link at the foot of a source card. Verified opens the page; Needs review
 * asks the person to check it, and says why.
 */
export function sourceAction(
  citation: Pick<Citation, "label" | "kind" | "detected_type" | "page_no">,
): SourceAction {
  const word = placeWord(citation).toLowerCase();
  const place = `${word} ${citation.page_no}`;
  if (citation.label === "verified") {
    return { text: `Open ${place}`, check: false, reason: null };
  }
  const what = citation.kind === "value" ? "This value" : "This text";
  return {
    text: `Check ${place}`,
    check: true,
    reason: `${what} was not fully checked when the document was read. Look at the ${word} before you rely on it.`,
  };
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
  const message = error.message.trim();
  if (error.status >= 400 && error.status < 500 && message && message !== GENERIC_MESSAGE) {
    return message;
  }
  return "Could not get an answer right now. Try again in a minute.";
}

// --- The open conversation lives in the URL (/chat?c=<id>) --------------------
// so a reload, or a link, brings the same conversation back. Only an id shaped
// like a UUID is read; the server still decides whether it is this person's.

const CONVERSATION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The conversation id in a location's query string, or null. */
export function conversationFromSearch(search: string): string | null {
  const id = new URLSearchParams(search).get("c");
  return id !== null && CONVERSATION_ID.test(id) ? id.toLowerCase() : null;
}

/** The chat address for a conversation, or the empty chat. */
export function chatHref(conversationId: string | null): string {
  return conversationId ? `/chat?c=${encodeURIComponent(conversationId)}` : "/chat";
}
