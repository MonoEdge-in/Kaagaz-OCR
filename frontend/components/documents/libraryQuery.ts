// The documents list, as a request and as a page address. Pure functions,
// unit tested in node (tests/library-query.test.ts).
//
// The page address (/documents?folder=…&tag=…) holds the folder and the
// filters, so a reload, a folder link from the viewer and Back all show the
// same view. Only well-formed values are read from it; the server still
// decides what this person may see.

import { query } from "@/lib/api";
import type { DetectedType, DocumentStatus } from "@/lib/types";
import { PAGE_SIZE } from "./logic";

/** GET /api/documents parameters (WP1.5 adds everything after q). */
export type DocumentListParams = {
  limit?: number;
  offset?: number;
  status?: DocumentStatus;
  q?: string;
  folder_id?: string; // a folder's id, or "root" for the top level only
  include_subfolders?: boolean;
  tag_id?: readonly string[]; // every tag must match; at most 50
  detected_type?: DetectedType;
  uploaded_from?: string; // YYYY-MM-DD, the first day included
  uploaded_to?: string; // YYYY-MM-DD, exclusive: the day after the last one
  trashed?: boolean; // WP1.6: true lists only what is in Trash; left out, only what is not
  attention?: boolean; // PRD 5 U2: true lists only documents with reading problems
};

/** What the documents screen shows: one folder, all documents or Trash, and the filters. */
export interface LibraryState {
  readonly folderId: string | null; // null: all documents, in every folder (or Trash)
  readonly subfolders: boolean; // in a folder: also the documents in its subfolders
  readonly trash: boolean; // Trash: every folder, only what is in Trash
  readonly tagIds: readonly string[]; // every tag must match
  readonly type: DetectedType | null;
  readonly from: string | null; // YYYY-MM-DD, the first day included
  readonly to: string | null; // YYYY-MM-DD, the last day included
  readonly attention: boolean; // PRD 5 U2 "Needs attention": only documents with reading problems
}

export const ALL_DOCUMENTS: LibraryState = {
  folderId: null,
  subfolders: false,
  trash: false,
  tagIds: [],
  type: null,
  from: null,
  to: null,
  attention: false,
};

/** The server takes at most this many tag_id values. */
export const MAX_TAG_FILTERS = 50;

/** The kinds the Type filter offers: the ones Kaagaz takes in (logic.ts ACCEPTED_EXTENSIONS). */
export const FILTER_TYPES: readonly DetectedType[] = ["pdf", "png", "jpeg", "tiff", "zip", "text"];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

function dateParts(text: string): [number, number, number] | null {
  const match = ISO_DATE.exec(text);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(Date.UTC(year, month - 1, day));
  const real =
    date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
  return real ? [year, month, day] : null;
}

/** A real calendar date written YYYY-MM-DD ("2026-02-30" is not one). */
export function isIsoDate(text: string): boolean {
  return dateParts(text) !== null;
}

/** The next calendar day, YYYY-MM-DD in and out. Only called with a real date. */
export function dayAfter(date: string): string {
  const parts = dateParts(date);
  if (!parts) throw new RangeError("dayAfter needs a YYYY-MM-DD date");
  const [year, month, day] = parts;
  return new Date(Date.UTC(year, month - 1, day + 1)).toISOString().slice(0, 10);
}

/** Tags, type, dates or Needs attention are set. The folder is where you are, not a filter. */
export function isFiltered(state: LibraryState): boolean {
  return (
    state.tagIds.length > 0 || state.type !== null || state.from !== null || state.to !== null || state.attention
  );
}

interface SearchLike {
  get(name: string): string | null;
  getAll(name: string): string[];
}

/** The view named in the page address; anything malformed is dropped. Trash has no folder. */
export function libraryFromSearch(search: SearchLike): LibraryState {
  const trash = search.get("trash") === "1";
  const folder = trash ? null : search.get("folder");
  const folderId = folder !== null && UUID.test(folder) ? folder.toLowerCase() : null;
  const tags = search
    .getAll("tag")
    .filter((id) => UUID.test(id))
    .map((id) => id.toLowerCase());
  const type = search.get("type");
  const from = search.get("from");
  const to = search.get("to");
  return {
    folderId,
    subfolders: folderId !== null && search.get("sub") === "1",
    trash,
    tagIds: [...new Set(tags)].slice(0, MAX_TAG_FILTERS),
    type: FILTER_TYPES.find((kind) => kind === type) ?? null,
    from: from !== null && isIsoDate(from) ? from : null,
    to: to !== null && isIsoDate(to) ? to : null,
    attention: !trash && search.get("attention") === "1",
  };
}

/** The page address for a view: "/documents" for all documents with no filter. */
export function libraryHref(state: LibraryState): string {
  return `/documents${query({
    trash: state.trash ? "1" : undefined,
    folder: state.folderId ?? undefined,
    sub: state.folderId !== null && state.subfolders ? "1" : undefined,
    tag: state.tagIds,
    type: state.type ?? undefined,
    from: state.from ?? undefined,
    to: state.to ?? undefined,
    attention: state.attention ? "1" : undefined,
  })}`;
}

/**
 * A folder with nothing of its own but documents in its subfolders looked empty for good,
 * with no Include subfolders box: its empty state offers this address instead (QA, 15 Oct
 * 2026). Null when the folder has no subfolders or they are already included.
 */
export function includeSubfoldersHref(state: LibraryState, subfolderCount: number): string | null {
  if (state.folderId === null || state.trash || state.subfolders || subfolderCount === 0) return null;
  return libraryHref({ ...state, subfolders: true });
}

/** The list request for a view, a search and a page. */
export function listParams(state: LibraryState, q: string, offset: number): DocumentListParams {
  return {
    limit: PAGE_SIZE,
    offset,
    trashed: state.trash ? true : undefined,
    q: q === "" ? undefined : q,
    folder_id: state.folderId ?? undefined,
    include_subfolders: state.folderId === null ? undefined : state.subfolders,
    tag_id: state.tagIds.length > 0 ? state.tagIds : undefined,
    detected_type: state.type ?? undefined,
    uploaded_from: state.from ?? undefined,
    // The screen's last day is included; the API's end is exclusive.
    uploaded_to: state.to === null ? undefined : dayAfter(state.to),
    attention: state.attention ? true : undefined,
  };
}
