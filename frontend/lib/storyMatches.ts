// "From your stories" (S3b): the user's own documents whose story the question
// tells again, shown beside a chat answer and above search results, never in
// them; and "Search my stories" in chat, which shows only them (council 3 Oct
// 2026, "story search mode"). The server sends ids, names, a type and a band;
// never the story and never a score. Pure functions, unit tested in node
// (tests/story-matches.test.ts).

import { postStoryMatches } from "./api";
import { docTypeLabel, isDocType } from "./format";
import type { ScopeBody, StoryBand, StoryMatch } from "./types";

export const MAX_STORY_CARDS = 3;
const MAX_STORY_QUESTION = 1000; // the route's q limit, a chat question's

export const STORY_HEADING = "From your stories";
export const STORY_NOTE_CHAT = "Based on stories only you wrote. Not part of the answer.";
export const STORY_NOTE_SEARCH = "Based on stories only you wrote. Not part of the results.";

const BAND_TEXT: Record<StoryBand, string> = {
  match: "Matches your story",
  maybe: "Might match your story",
};

// "Search my stories": its own words, so it never reads like an answer.
export const SEARCH_HEADING = "Search my stories";
export const SEARCH_CARDS_HEADING = "Documents your stories match";
export const SEARCH_NOTE = "Searched only stories you wrote, not the documents. This is not an answer.";
export const SEARCH_EMPTY =
  "No document matches your stories. Try other words, or write a story on the document first.";
export const SEARCH_NO_WORDS = "Add a name, place or year from your story";
export const SEARCH_ERROR = "Could not search your stories. Try again.";
export const SEARCH_PENDING = "Searching your stories\u2026"; // an ellipsis
export const SEARCH_BANDS: Record<StoryBand, string> = {
  match: "Your story matches",
  maybe: "Might be this one",
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The scope fields POST /api/me/story-matches takes: the chat's ScopeBody. */
export type StoryScope = ScopeBody;
export type StoryMode = "cards" | "stories";

export type StoryMatchBody = StoryScope & { q: string; limit?: number; mode?: StoryMode };

/**
 * The request for a question and the chat's scope, or null when there is
 * nothing to ask. A field left out filters nothing; include_subfolders goes
 * only with a folder. An empty list would match nothing, so no call is made.
 */
export function storyMatchParams(question: string, scope: StoryScope = {}): StoryMatchBody | null {
  const q = question.trim().slice(0, MAX_STORY_QUESTION);
  if (!q) return null;
  const lists = [scope.tag_ids, scope.doc_types, scope.document_ids];
  if (lists.some((list) => list != null && list.length === 0)) return null;
  return {
    q,
    folder_id: scope.folder_id,
    include_subfolders: scope.folder_id == null ? undefined : scope.include_subfolders,
    tag_ids: scope.tag_ids,
    doc_types: scope.doc_types,
    document_ids: scope.document_ids,
    date_from: scope.date_from,
    date_to: scope.date_to,
  };
}

function isStoryBand(value: unknown): value is StoryBand {
  return value === "match" || value === "maybe";
}

function storyMatch(item: unknown): StoryMatch | null {
  if (typeof item !== "object" || item === null) return null;
  const { document_id: id, display_name: name, doc_type: type, band } = item as Record<string, unknown>;
  if (typeof id !== "string" || !UUID.test(id)) return null;
  if (typeof name !== "string" || !name.trim()) return null;
  if (!isStoryBand(band)) return null;
  if (type !== null && typeof type !== "string") return null;
  return { document_id: id, display_name: name, doc_type: isDocType(type) ? type : null, band };
}

/**
 * The matches to show from a reply's `story_matches`: malformed items, unknown
 * bands and repeats dropped; every "match" before every "maybe", the server's
 * order kept otherwise; at most `most` (three side cards).
 */
export function storyMatchesFrom(raw: unknown, most = MAX_STORY_CARDS): StoryMatch[] {
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const kept: StoryMatch[] = [];
  for (const item of raw) {
    const match = storyMatch(item);
    if (match === null || seen.has(match.document_id)) continue;
    seen.add(match.document_id);
    kept.push(match);
  }
  const ordered = [
    ...kept.filter((match) => match.band === "match"),
    ...kept.filter((match) => match.band === "maybe"),
  ];
  return ordered.slice(0, most);
}

/** One card as the screen shows it. Every text is plain text, never markup. */
export interface StoryCard {
  id: string;
  name: string;
  type: string;
  band: string; // the band in words, so colour is never the only signal
  href: string;
  lead: boolean; // the one strong match: first, larger, "Open document"
  openText: string;
}

/**
 * The cards, in order. When exactly one is a "match", it leads: first, larger,
 * with an "Open document" button. Nothing ever opens by itself.
 */
export function storyCards(matches: readonly StoryMatch[], bands = BAND_TEXT): StoryCard[] {
  const strong = matches.filter((match) => match.band === "match").length === 1;
  const ordered = strong
    ? [...matches.filter((match) => match.band === "match"), ...matches.filter((match) => match.band !== "match")]
    : matches;
  return ordered.map((match) => {
    const lead = strong && match.band === "match";
    return {
      id: match.document_id,
      name: match.display_name,
      type: docTypeLabel(match.doc_type),
      band: bands[match.band],
      href: `/documents/${encodeURIComponent(match.document_id)}`,
      lead,
      openText: lead ? "Open document" : "Open",
    };
  });
}

/** What the polite status line says once the cards arrive; "" for none. */
export function storyStatusText(count: number): string {
  if (count <= 0) return "";
  return count === 1 ? "1 document matches your stories." : `${count} documents match your stories.`;
}

/**
 * Only the latest request may show: next() starts one (and so retires every
 * earlier one), isLatest() says whether a reply still belongs on the screen.
 */
export function latestOnly(): { next(): number; isLatest(token: number): boolean } {
  let current = 0;
  return {
    next: () => ++current,
    isLatest: (token) => token === current,
  };
}

/**
 * The side cards' checked matches for a question in the chip's scope, or [] on
 * any failure: this call never affects the answer, so it never throws and shows
 * no error.
 */
export async function findStoryMatches(
  question: string,
  scope: StoryScope = {},
  signal?: AbortSignal,
  post: typeof postStoryMatches = postStoryMatches,
): Promise<StoryMatch[]> {
  const params = storyMatchParams(question, scope);
  if (params === null) return [];
  try {
    const reply = await post(params, signal);
    const raw = typeof reply === "object" && reply !== null ? (reply as Record<string, unknown>).story_matches : null;
    return storyMatchesFrom(raw);
  } catch {
    return [];
  }
}

/** "Search my stories": the matches, how many stories were read, and whether that was all. */
export interface StorySearch {
  matches: StoryMatch[];
  searched: number;
  all: boolean;
  noContentWords: boolean; // the question had no name, place or year to look for
}

/**
 * "Search my stories" for a question in the chip's scope. Unlike the side cards
 * it throws on any failure (an abort too, and a reply of the wrong shape), so an
 * error never looks like "no match".
 */
export async function searchStoryMatches(
  question: string,
  scope: StoryScope,
  limit: number,
  mode: StoryMode,
  signal?: AbortSignal,
  post: typeof postStoryMatches = postStoryMatches,
): Promise<StorySearch> {
  const params = storyMatchParams(question, scope);
  if (params === null) return { matches: [], searched: 0, all: true, noContentWords: false };
  const reply = await post({ ...params, limit, mode }, signal);
  const body = typeof reply === "object" && reply !== null ? (reply as Record<string, unknown>) : {};
  const { story_matches: raw, searched, all, reason } = body;
  if (!Array.isArray(raw) || typeof searched !== "number" || typeof all !== "boolean") {
    throw new Error("story_matches_shape");
  }
  return { matches: storyMatchesFrom(raw, limit), searched, all, noContentWords: reason === "no_content_words" };
}

/** The line under the results when only the newest stories were read; null when all were. */
export function scanNote(found: StorySearch): string | null {
  return found.all ? null : `Searched your ${found.searched} most recently changed stories`;
}

/** What the status line says once a story search is done. */
export function searchStatusText(found: StorySearch): string {
  if (found.noContentWords) return SEARCH_NO_WORDS;
  return found.matches.length === 0 ? "No document matches your stories." : storyStatusText(found.matches.length);
}
