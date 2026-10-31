// Search inside documents (PRD 3 Q3): the request for a view and a question,
// and what one result shows. Pure functions, unit tested in node
// (tests/inside-search.test.ts).
//
// "Inside documents" (the default) searches the documents' text through
// GET /api/search; "Names" is the list's own name search (q). The folder, tag
// and Type filters apply to both. The upload dates are the list's only: search
// filters on the document's own date, which is a different question.
// File management Q3, Q5: the department, owner, label, party, property, document date and
// "has a newer version" filters go to both too; each result shows its version, label chip,
// folder path with the page, and its passage with the words searched for marked; "Also
// searched: … (from your words)" names the person's own words the search used (Q2).

import { linkWords } from "@/components/chat/answer";
import { docTypeLabel } from "@/lib/format";
import { pageLink } from "@/lib/regions";
import type { DetectedType, DocumentSearch, FoundDocument, Sensitivity } from "@/lib/types";
import { fileFilterParams, isFiltered } from "./libraryQuery";
import type { LibraryState } from "./libraryQuery";

export type SearchMode = "inside" | "names";

// #468: a search that finds nothing offers Ask with the same words, filled in, never sent
// (/chat?q= without send=1 puts them in the box: ChatScreen, questionFromSearch).

/** The chat address with these words in the question box. */
export function askWithWordsHref(q: string): string {
  return `/chat?q=${encodeURIComponent(q)}`;
}

const ASK_LABEL_WORDS = 40; // a long search is cut short on the button

/** "Ask Kaagaz about “rent”". */
export function askAboutLabel(q: string): string {
  const words = q.length > ASK_LABEL_WORDS ? `${q.slice(0, ASK_LABEL_WORDS)}…` : q;
  return `Ask Kaagaz about “${words}”`;
}

export const DEFAULT_SEARCH_MODE: SearchMode = "inside";
/** The longest search both routes take (q, 200 characters). */
export const MAX_SEARCH_LENGTH = 200;

export const SEARCH_MODES: readonly { mode: SearchMode; label: string }[] = [
  { mode: "inside", label: "Inside documents" },
  { mode: "names", label: "Names" },
];

/** GET /api/search parameters (backend/kaagaz/api/routers/search.py). */
export type DocumentSearchParams = {
  q: string;
  folder_id?: string;
  include_subfolders?: boolean;
  tag_ids?: readonly string[]; // every tag must match; at most 50
  detected_type?: DetectedType; // the Type filter: the file type
  // File management Q3 (stage 3), through the search scope.
  department_id?: string;
  owner_id?: string;
  sensitivity?: readonly Sensitivity[];
  party?: string;
  property?: string;
  doc_date_from?: string;
  doc_date_to?: string;
  has_newer_version?: boolean;
};

/** Trash is left out of search, so it only has the name search. */
export function searchModeFor(mode: SearchMode, state: LibraryState): SearchMode {
  return state.trash ? "names" : mode;
}

/** The search request for a view and a question (already trimmed, not empty). */
export function searchParams(state: LibraryState, q: string): DocumentSearchParams {
  const filters = fileFilterParams(state);
  return {
    q,
    folder_id: state.folderId ?? undefined,
    include_subfolders: state.folderId === null ? undefined : state.subfolders,
    tag_ids: state.tagIds.length > 0 ? state.tagIds : undefined,
    detected_type: state.type ?? undefined,
    department_id: filters.department_id,
    owner_id: filters.owner_id,
    sensitivity: filters.sensitivity,
    party: filters.party,
    property: filters.property,
    doc_date_from: filters.doc_date_from,
    doc_date_to: filters.doc_date_to,
    has_newer_version: filters.has_newer_version,
  };
}

/** "Page 15", or "Pages 15 to 16" for a piece across two pages. */
export function pagesText(from: number, to: number): string {
  return to > from ? `Pages ${from} to ${to}` : `Page ${from}`;
}

/** One result as the screen shows it. Every text is plain text, never markup. */
export interface ResultView {
  id: string;
  name: string;
  type: string;
  snippet: string;
  pages: string;
  href: string; // the viewer, on the first page with the spot marked and the words searched lit
  version: string | null; // "v2", "v2 of 3"; null when the reply does not say
  label: Sensitivity | null;
  place: string; // "Land & Legal / Land records · Page 2"
}

export function resultView(found: FoundDocument, q = ""): ResultView {
  const { best } = found;
  const region = best.regions.find((spot) => spot.page === best.page_from) ?? best.regions[0];
  const pages = pagesText(best.page_from, best.page_to);
  const version =
    found.version_no === undefined
      ? null
      : found.versions !== undefined && found.versions > 1
        ? `v${found.version_no} of ${found.versions}`
        : `v${found.version_no}`;
  return {
    id: found.id,
    name: found.display_name,
    type: docTypeLabel(found.doc_type),
    snippet: best.snippet,
    pages,
    href: pageLink(found.id, region?.page ?? best.page_from, region, { words: linkWords(q) }),
    version,
    label: found.sensitivity ?? null,
    place: found.folder_path ? `${found.folder_path} · ${pages}` : pages,
  };
}

/** What the results say when nothing was found: the folder, tags, type and filters narrow a search. */
export function noResultsText(state: LibraryState): string {
  // The upload dates and Needs attention narrow the list of names only.
  const narrowed = state.folderId !== null || isFiltered({ ...state, from: null, to: null, attention: false });
  return narrowed ? "No document here holds these words." : "No document holds these words.";
}

/**
 * How many documents were found. A list cut at the server's cap says so (issue 374): it is
 * the first of more, never the whole count.
 */
export function foundCountText(found: Pick<DocumentSearch, "documents" | "capped">): string {
  const count = found.documents.length;
  if (found.capped) return `Showing the first ${count} documents found. More matched: add a word or a filter to narrow the search.`;
  return count === 1 ? "1 document found." : `${count} documents found.`;
}

/** "Also searched: 7/12 (from your words)"; null when the search used none of them (Q2). */
export function alsoSearchedText(found: Pick<DocumentSearch, "understood_words">): string | null {
  const meanings = [...new Set((found.understood_words ?? []).map((word) => word.meaning.trim()).filter(Boolean))];
  return meanings.length === 0 ? null : `Also searched: ${meanings.join(", ")} (from your words)`;
}

/**
 * The passage in pieces, each marked when it is one of the words searched for (any letter
 * case), so the screen marks them with <mark> and never builds markup from text. Words of
 * one letter are left out. O(n · t) for t words.
 */
export function highlightParts(snippet: string, words: readonly string[]): { text: string; hit: boolean }[] {
  const terms = [...new Set(words.flatMap((word) => word.split(/\s+/)).filter((term) => term.length > 1))];
  if (terms.length === 0 || snippet === "") return [{ text: snippet, hit: false }];
  const escaped = terms
    .sort((a, b) => b.length - a.length)
    .map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(`(${escaped.join("|")})`, "giu");
  return snippet
    .split(pattern)
    .filter((part) => part !== "")
    .map((part) => ({ text: part, hit: terms.some((term) => term.toLowerCase() === part.toLowerCase()) }));
}
