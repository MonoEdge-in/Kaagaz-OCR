// The documents list, as a request and as a page address. Pure functions,
// unit tested in node (tests/library-query.test.ts).
//
// The page address (/documents?folder=…&tag=…) holds the folder and the
// filters, so a reload, a folder link from the viewer and Back all show the
// same view. Only well-formed values are read from it; the server still
// decides what this person may see.
// File management (PRD 1, Phase 2): Q3's filters (department, owner, labels, party,
// property, document dates, has a newer version) and Q4's sort live in it too.

import { isLabel } from "@/components/files/labels";
import { query } from "@/lib/api";
import type { DetectedType, DocumentStatus, Sensitivity } from "@/lib/types";
import { PAGE_SIZE } from "./logic";

/** Q4: what the list sorts by. created_at (newest first) is the default. */
export type SortKey =
  | "name"
  | "doc_type"
  | "owner"
  | "sensitivity"
  | "version"
  | "document_date"
  | "updated_at"
  | "created_at";
export type SortOrder = "asc" | "desc";
export const SORT_KEYS: readonly SortKey[] = [
  "name",
  "doc_type",
  "owner",
  "sensitivity",
  "version",
  "document_date",
  "updated_at",
  "created_at",
];

/** GET /api/documents parameters (WP1.5 adds everything after q; File management Q3, Q4 the rest). */
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
  department_id?: string;
  owner_id?: string;
  sensitivity?: readonly Sensitivity[]; // any of them
  party?: string;
  property?: string; // a survey number
  doc_date_from?: string; // YYYY-MM-DD, both days included
  doc_date_to?: string;
  has_newer_version?: boolean;
  sort?: SortKey;
  order?: SortOrder;
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
  // File management Q3.
  readonly departmentId: string | null; // a department's id, or NO_DEPARTMENT
  readonly ownerId: string | null;
  readonly labels: readonly Sensitivity[];
  readonly party: string | null;
  readonly property: string | null;
  readonly docFrom: string | null; // the document's own date, both days included
  readonly docTo: string | null;
  readonly newer: boolean; // only families with a newer version waiting to be confirmed
  // Q4.
  readonly sort: SortKey;
  readonly order: SortOrder;
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
  departmentId: null,
  ownerId: null,
  labels: [],
  party: null,
  property: null,
  docFrom: null,
  docTo: null,
  newer: false,
  sort: "created_at",
  order: "desc",
};

/** Every filter off, the folder and the sort kept: Clear all. */
export const NO_FILTERS: Partial<LibraryState> = {
  tagIds: [],
  type: null,
  from: null,
  to: null,
  attention: false,
  departmentId: null,
  ownerId: null,
  labels: [],
  party: null,
  property: null,
  docFrom: null,
  docTo: null,
  newer: false,
};

/** The department filter for the files in no department, as the dashboard counts them. */
export const NO_DEPARTMENT = "none";

/** The server takes at most this many tag_id values. */
export const MAX_TAG_FILTERS = 50;
/** The longest party name and survey number the server takes (Q3). */
export const MAX_PARTY_LENGTH = 200;
export const MAX_PROPERTY_LENGTH = 64;

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

/** How many filters are set, each chip counting once. The folder is where you are, not a filter. */
export function filterCount(state: LibraryState): number {
  return [
    state.tagIds.length > 0,
    state.type !== null,
    state.from !== null || state.to !== null,
    state.attention,
    state.departmentId !== null,
    state.ownerId !== null,
    state.labels.length > 0,
    state.party !== null,
    state.property !== null,
    state.docFrom !== null || state.docTo !== null,
    state.newer,
  ].filter(Boolean).length;
}

/** Any filter is set. The folder is where you are, not a filter; nor is the sort. */
export function isFiltered(state: LibraryState): boolean {
  return filterCount(state) > 0;
}

interface SearchLike {
  get(name: string): string | null;
  getAll(name: string): string[];
}

const words = (text: string | null, max: number) => {
  const clean = (text ?? "").trim().replace(/\s+/g, " ");
  return clean === "" ? null : clean.slice(0, max);
};
const day = (text: string | null) => (text !== null && isIsoDate(text) ? text : null);
const uuid = (text: string | null) => (text !== null && UUID.test(text) ? text.toLowerCase() : null);

/** The view named in the page address; anything malformed is dropped. Trash has no folder. */
export function libraryFromSearch(search: SearchLike): LibraryState {
  const trash = search.get("trash") === "1";
  const folderId = trash ? null : uuid(search.get("folder"));
  const tags = search
    .getAll("tag")
    .filter((id) => UUID.test(id))
    .map((id) => id.toLowerCase());
  const type = search.get("type");
  const sort = search.get("sort");
  return {
    folderId,
    subfolders: folderId !== null && search.get("sub") === "1",
    trash,
    tagIds: [...new Set(tags)].slice(0, MAX_TAG_FILTERS),
    type: FILTER_TYPES.find((kind) => kind === type) ?? null,
    from: day(search.get("from")),
    to: day(search.get("to")),
    attention: !trash && search.get("attention") === "1",
    departmentId: search.get("dept") === NO_DEPARTMENT ? NO_DEPARTMENT : uuid(search.get("dept")),
    ownerId: uuid(search.get("owner")),
    labels: [...new Set(search.getAll("label").filter(isLabel))],
    party: words(search.get("party"), MAX_PARTY_LENGTH),
    property: words(search.get("property"), MAX_PROPERTY_LENGTH),
    docFrom: day(search.get("dfrom")),
    docTo: day(search.get("dto")),
    newer: !trash && search.get("newer") === "1",
    sort: SORT_KEYS.find((key) => key === sort) ?? "created_at",
    order: search.get("order") === "asc" ? "asc" : "desc",
  };
}

/** The page address for a view: "/documents" for all documents with no filter. */
export function libraryHref(state: LibraryState): string {
  const defaultSort = state.sort === "created_at" && state.order === "desc";
  return `/documents${query({
    trash: state.trash ? "1" : undefined,
    folder: state.folderId ?? undefined,
    sub: state.folderId !== null && state.subfolders ? "1" : undefined,
    tag: state.tagIds,
    type: state.type ?? undefined,
    from: state.from ?? undefined,
    to: state.to ?? undefined,
    attention: state.attention ? "1" : undefined,
    dept: state.departmentId ?? undefined,
    owner: state.ownerId ?? undefined,
    label: state.labels,
    party: state.party ?? undefined,
    property: state.property ?? undefined,
    dfrom: state.docFrom ?? undefined,
    dto: state.docTo ?? undefined,
    newer: state.newer ? "1" : undefined,
    sort: defaultSort ? undefined : state.sort,
    order: defaultSort ? undefined : state.order,
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

/** Q3's filters as request parameters, shared by the list and the search; only what is set. */
export function fileFilterParams(state: LibraryState): DocumentListParams {
  return {
    department_id: state.departmentId ?? undefined,
    owner_id: state.ownerId ?? undefined,
    sensitivity: state.labels.length > 0 ? state.labels : undefined,
    party: state.party ?? undefined,
    property: state.property ?? undefined,
    doc_date_from: state.docFrom ?? undefined,
    doc_date_to: state.docTo ?? undefined,
    has_newer_version: state.newer ? true : undefined,
  };
}

/** The list request for a view, a search and a page. */
export function listParams(state: LibraryState, q: string, offset: number): DocumentListParams {
  const defaultSort = state.sort === "created_at" && state.order === "desc";
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
    ...fileFilterParams(state),
    sort: defaultSort ? undefined : state.sort,
    order: defaultSort ? undefined : state.order,
  };
}

/**
 * A column header pressed (Q4): the same column flips its order; a new one starts A to Z for
 * names and words, newest first for dates and versions.
 */
export function nextSort(state: Pick<LibraryState, "sort" | "order">, key: SortKey): { sort: SortKey; order: SortOrder } {
  if (state.sort === key) return { sort: key, order: state.order === "asc" ? "desc" : "asc" };
  const newestFirst = key === "document_date" || key === "updated_at" || key === "created_at" || key === "version";
  return { sort: key, order: newestFirst ? "desc" : "asc" };
}
