// Search inside documents (PRD 3 Q3): the request for a view and a question,
// and what one result shows. Pure functions, unit tested in node
// (tests/inside-search.test.ts).
//
// "Inside documents" (the default) searches the documents' text through
// GET /api/search; "Names" is the list's own name search (q). The folder, tag
// and Type filters apply to both. The upload dates are the list's only: search
// filters on the document's own date, which is a different question.

import { docTypeLabel } from "@/lib/format";
import { pageLink } from "@/lib/regions";
import type { DetectedType, FoundDocument } from "@/lib/types";
import type { LibraryState } from "./libraryQuery";

export type SearchMode = "inside" | "names";

export const DEFAULT_SEARCH_MODE: SearchMode = "inside";

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
};

/** Trash is left out of search, so it only has the name search. */
export function searchModeFor(mode: SearchMode, state: LibraryState): SearchMode {
  return state.trash ? "names" : mode;
}

/** The search request for a view and a question (already trimmed, not empty). */
export function searchParams(state: LibraryState, q: string): DocumentSearchParams {
  return {
    q,
    folder_id: state.folderId ?? undefined,
    include_subfolders: state.folderId === null ? undefined : state.subfolders,
    tag_ids: state.tagIds.length > 0 ? state.tagIds : undefined,
    detected_type: state.type ?? undefined,
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
  href: string; // the viewer, on the first page with the spot marked
}

export function resultView(found: FoundDocument): ResultView {
  const { best } = found;
  const region = best.regions.find((spot) => spot.page === best.page_from) ?? best.regions[0];
  return {
    id: found.id,
    name: found.display_name,
    type: docTypeLabel(found.doc_type),
    snippet: best.snippet,
    pages: pagesText(best.page_from, best.page_to),
    href: pageLink(found.id, region?.page ?? best.page_from, region),
  };
}

/** What the results say when nothing was found: the folder, tags and type narrow a search. */
export function noResultsText(state: LibraryState): string {
  const narrowed = state.folderId !== null || state.tagIds.length > 0 || state.type !== null;
  return narrowed ? "No document here holds these words." : "No document holds these words.";
}
