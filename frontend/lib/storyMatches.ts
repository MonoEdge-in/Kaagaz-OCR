// "From your stories" (S3b): the user's own documents whose story the question
// tells again, shown beside a chat answer and above search results, never in
// them. The server sends ids, names, a type and a band; never the story and
// never a score. Pure functions, unit tested in node (tests/story-matches.test.ts).

import { getStoryMatches } from "./api";
import { docTypeLabel, isDocType } from "./format";
import type { StoryBand, StoryMatch } from "./types";

export const MAX_STORY_CARDS = 3;
const MAX_STORY_QUESTION = 1000; // the route's q limit, a chat question's

export const STORY_HEADING = "From your stories";
export const STORY_NOTE_CHAT = "Based on stories only you wrote. Not part of the answer.";
export const STORY_NOTE_SEARCH = "Based on stories only you wrote. Not part of the results.";

const BAND_TEXT: Record<StoryBand, string> = {
  match: "Matches your story",
  maybe: "Might match your story",
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The scope fields GET /api/me/story-matches takes, as the chat's ScopeBody. */
export type StoryScope = {
  folder_id?: string;
  include_subfolders?: boolean;
  tag_ids?: readonly string[];
  doc_types?: readonly string[];
  document_ids?: readonly string[];
  date_from?: string; // YYYY-MM-DD
  date_to?: string;
};

export type StoryMatchParams = StoryScope & { q: string };

/**
 * The request for a question and the chat's scope, or null when there is
 * nothing to ask. A field left out filters nothing; include_subfolders goes
 * only with a folder. An empty list would match nothing, so no call is made.
 */
export function storyMatchParams(question: string, scope: StoryScope = {}): StoryMatchParams | null {
  const q = question.trim().slice(0, MAX_STORY_QUESTION);
  if (!q) return null;
  const lists = [scope.tag_ids, scope.doc_types, scope.document_ids];
  if (lists.some((list) => list !== undefined && list.length === 0)) return null;
  return {
    q,
    folder_id: scope.folder_id,
    include_subfolders: scope.folder_id === undefined ? undefined : scope.include_subfolders,
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
 * order kept otherwise; at most three.
 */
export function storyMatchesFrom(raw: unknown): StoryMatch[] {
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
  return ordered.slice(0, MAX_STORY_CARDS);
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
export function storyCards(matches: readonly StoryMatch[]): StoryCard[] {
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
      band: BAND_TEXT[match.band],
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
 * The checked matches for a question, or [] on any failure: this call never
 * affects the answer, so it never throws and shows no error.
 */
export async function findStoryMatches(
  question: string,
  scope: StoryScope = {},
  signal?: AbortSignal,
  get: typeof getStoryMatches = getStoryMatches,
): Promise<StoryMatch[]> {
  const params = storyMatchParams(question, scope);
  if (params === null) return [];
  try {
    const reply = await get(params, signal);
    const raw = typeof reply === "object" && reply !== null ? (reply as Record<string, unknown>).story_matches : null;
    return storyMatchesFrom(raw);
  } catch {
    return [];
  }
}
