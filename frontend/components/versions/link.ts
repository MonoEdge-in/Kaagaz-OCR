// Linking files already uploaded as versions (#648, V13 to V18, V-C): every word of the link
// dialog, the Possible versions view and the Kept apart list, in one place, and the pure
// functions behind them (tests/versions-link-648.test.ts). Only Admins (in an admin session: the
// confirm step names people) and Management see them; the server checks every rule again.

import { ApiError, undoLinkVersions } from "@/lib/api";
import type {
  DocumentSummary,
  LinkPreview,
  LinkVersionsBody,
  Me,
  VersionGroup,
  VersionGroupFile,
  VersionGroupReason,
} from "@/lib/types";
import { identifierWords, joinAnd, sharePercent } from "./words";

export const LINK_TEXT = {
  title: "Link as versions",
  steps: ["Pick the files", "Put them in order", "Check and link"] as const,
  step: (n: number) => `Step ${n} of 3`,
  findLabel: "Find files by name",
  findPlaceholder: "e.g. Lease agreement",
  find: "Search",
  finding: "Searching…",
  noneFound: "No files with that name.",
  add: "Add",
  remove: "Remove",
  chosen: (n: number) => `Chosen (${n})`,
  pickHint: "Pick 2 to 20 files. A file that already has versions brings them all.",
  orderHint: "Oldest first. Drag a file, or use its arrows, to change the order.",
  moveUp: "Move up",
  moveDown: "Move down",
  latest: "This is the latest",
  latestHint: "Ask answers from the latest.",
  next: "Next",
  back: "Back",
  cancel: "Cancel",
  link: "Link as versions",
  linking: "Linking…",
  checking: "Checking…",
  passwordNote: "Type your password to go on.",
  password: "Your password",
  linked: (n: number, name: string) => `Linked ${n} files as versions. Ask answers from "${name}".`,
  undone: "Undone. The files are back as they were.",
  undoInTab: "To undo this, open the file and use Undo linking in its Versions tab.",
  connection: "Check your connection, then try again.",
  // The library and the Versions tab (the row menu's words are in documents/library.ts).
  addVersion: "Add an older or newer version",
  undoLink: "Undo linking",
  bulk: (n: number) => `Link as versions (${n})`,
  // The Possible versions view (V17) and the Kept apart list (V18).
  possible: "Possible versions",
  possibleCount: (n: number, more: boolean) => `Possible versions (${n}${more ? "+" : ""})`,
  possibleIntro: "Kaagaz found files that may be versions of each other. Tick the ones that are, then link them.",
  onlyAdmins: "Only Admins and Management can see possible versions.",
  noGroups: "No possible versions right now",
  noGroupsText: "Kaagaz looks through your files once, then checks each new upload.",
  loading: "Loading possible versions…",
  loadFailed: "Could not load possible versions.",
  groupHeading: (n: number) => `${n} files that look alike`,
  laterNote: "Left for later",
  more: "Showing the first 200 groups. Decide on these to see the rest.",
  why: "Why",
  noneAre: "None of these are versions",
  keptApart: (n: number) => (n === 1 ? "Kept apart. It won't be suggested with these again." : `Kept ${n} files apart.`),
  compare: "Compare",
  compareWith: "with",
  pickTwo: "Pick two different files.",
  notLinkedHeading: "Files not linked",
  notLinked: (name: string) => `"${name}" was not linked. Is it a version of these?`,
  notVersion: "Not a version of these",
  decideLater: "Decide later",
  keptApartHeading: "Kept apart",
  keptApartIntro: "Files someone said are not versions. Link them, or undo it, if that was a mistake.",
  noKeptApart: "Nothing is kept apart.",
  pair: (a: string, b: string) => `"${a}" and "${b}"`,
  separated: "Made separate",
  keptSeparate: "Kept separate",
  notVersions: "Marked as not versions",
  undo: "Undo",
  notMarked: (n: number) =>
    n === 1 ? "Undone. It's no longer marked as not a version." : "Undone. They're no longer marked as not versions.",
};

/** The Possible versions view and its Kept apart list (V17, V18). */
export const POSSIBLE_PATH = "/documents/possible-versions";

/** The most and fewest files one link takes (the API's 2 to 20). */
export const MAX_LINK = 20;
export const MIN_LINK = 2;

/** Admins in an admin session (the server asks every Admin for one, Management or not) and
 * Management; never anyone else. */
export function mayLinkVersions(me: Me): boolean {
  return me.user.role === "admin" ? me.session.scope === "admin" : me.access?.management === true;
}

/** A file in the link dialog: `date` is its own date, else the upload date (ISO). */
export interface LinkFile {
  id: string;
  name: string;
  date: string;
  familyId?: string;
  folderId?: string | null;
  pages?: number | null;
}

export const fromSummary = (document: DocumentSummary): LinkFile => ({
  id: document.id,
  name: document.display_name,
  date: document.document_date ?? document.created_at,
  familyId: document.family_id,
  folderId: document.folder_id,
  pages: document.page_count,
});

export const fromGroupFile = (file: VersionGroupFile): LinkFile => ({
  id: file.id,
  name: file.name,
  date: file.document_date ?? file.uploaded_at,
  folderId: file.folder_id,
  pages: file.page_count,
});

/** A group's "Link as versions" (the lead's flexible group): its ticked files in its order, the
 * suggested latest only while it is ticked (else null: the dialog starts on the newest ticked,
 * review of #659), and the files left unticked, to ask about after the link. */
export function groupLink(
  files: readonly VersionGroupFile[],
  ticked: ReadonlySet<string>,
  suggested: string | null,
): { files: LinkFile[]; latest: string | null; unticked: { id: string; name: string }[] } {
  return {
    files: files.filter((file) => ticked.has(file.id)).map(fromGroupFile),
    latest: suggested !== null && ticked.has(suggested) ? suggested : null,
    unticked: files.filter((file) => !ticked.has(file.id)).map((file) => ({ id: file.id, name: file.name })),
  };
}

/** Oldest first (V13), by the document's own date, else the upload date; ties keep their place. */
export function byDate<T extends Pick<LinkFile, "date">>(files: readonly T[]): T[] {
  const when = (file: T) => Date.parse(file.date) || 0;
  return files.map((file, at) => ({ file, at })).sort((a, b) => when(a.file) - when(b.file) || a.at - b.at).map(({ file }) => file);
}

/** The order with `id` moved one place up (-1) or down (1); unchanged at either end. */
export function moved(order: readonly string[], id: string, by: -1 | 1): string[] {
  const at = order.indexOf(id);
  const to = at + by;
  if (at === -1 || to < 0 || to >= order.length) return [...order];
  const next = order.filter((each) => each !== id);
  next.splice(to, 0, id);
  return next;
}

/** The order with `id` dropped at the place of `onto` (a drag). */
export function movedTo(order: readonly string[], id: string, onto: string): string[] {
  if (id === onto || !order.includes(id) || !order.includes(onto)) return [...order];
  const without = order.filter((each) => each !== id);
  const at = without.indexOf(onto) + (order.indexOf(id) < order.indexOf(onto) ? 1 : 0);
  return [...without.slice(0, at), id, ...without.slice(at)];
}

/** The selection's "Link as versions" (V13): the ticked files not locked (a locked file is never
 * linked), when 2 to 20; else none. */
export function linkableIds(items: readonly Pick<DocumentSummary, "id" | "locked">[], ticked: ReadonlySet<string>): string[] {
  const ids = items.filter((item) => ticked.has(item.id) && item.locked !== true).map((item) => item.id);
  return ids.length >= MIN_LINK && ids.length <= MAX_LINK ? ids : [];
}

/** The link of the files in the order shown; the latest picked when it is one of them, else the
 * newest. null outside 2 to 20 files. */
export function linkBody(order: readonly string[], latest: string | null): LinkVersionsBody | null {
  const ids = [...order];
  if (ids.length < MIN_LINK || ids.length > MAX_LINK) return null;
  const current = latest !== null && ids.includes(latest) ? latest : ids.at(-1);
  return current === undefined ? null : { document_ids: ids, order: ids, current_id: current };
}

// The lines of kaagaz/versions.py: the text is said from TEXT_FLOOR, the name from NAME_SIGNAL.
const TEXT_FLOOR = 0.5;
const NAME_LINE = 0.6;

/**
 * Why a file of a group is there (the lead, 16 Nov 2026): "94% same text as X · same name",
 * "81% same text as X · 79% as Y". Its reasons strongest text first, each named by the other
 * file; "" for none.
 */
export function reasonLine(reasons: readonly VersionGroupReason[], nameOf: (id: string) => string): string {
  const text = (reason: VersionGroupReason) => reason.text_overlap ?? -1;
  const parts: string[] = [];
  let saidText = false;
  for (const reason of [...reasons].sort((a, b) => text(b) - text(a))) {
    const name = nameOf(reason.with);
    const others: string[] = [];
    if ((reason.name_similarity ?? 0) >= NAME_LINE) others.push((reason.name_similarity ?? 0) >= 1 ? "same name" : "similar name");
    if (reason.shared_identifier) others.push(`same ${identifierWords(reason.shared_identifier)}`);
    if (reason.text_overlap !== null && reason.text_overlap >= TEXT_FLOOR) {
      const about = reason.text_overlap_estimated ? "about " : "";
      const share = sharePercent(reason.text_overlap, reason.text_overlap_estimated);
      parts.push(`${about}${share}% ${saidText ? "" : "same text "}as ${name}`);
      saidText = true;
      parts.push(...others);
    } else if (others.length > 0) {
      parts.push(`${others[0]} as ${name}`, ...others.slice(1));
    }
  }
  return parts.join(" · ");
}

/** The confirm step in plain words (V13 step 3, V15). */
export function confirmLines(preview: LinkPreview, files: number): string[] {
  const where = preview.held
    ? `They'll be kept where ${preview.latest.name} is, held for one person.`
    : preview.folder?.path
      ? `They'll be kept in ${preview.folder.path}.`
      : "They'll be kept at the top level, in no folder.";
  const lines = [
    `These ${files} files become one file with ${preview.versions} versions.`,
    `Ask will answer from the latest: ${preview.latest.name}.`,
    where,
  ];
  const losing = preview.losing.length;
  if (losing > 0) {
    const who = joinAnd(preview.losing.map((person) => person.name));
    lines.push(`${losing} ${losing === 1 ? "person" : "people"} will lose access: ${who}.`);
  }
  return lines;
}

/** The server's words for a refusal, else a hint about the connection. */
export function linkErrorText(caught: unknown): string {
  return caught instanceof ApiError ? caught.message : LINK_TEXT.connection;
}

/** A refusal that asks for the person's password (an Admin opening a file to themselves). */
export const needsPassword = (caught: unknown) => caught instanceof ApiError && caught.code === "password_required";

/** The toast's Undo of a link (V16): the Versions tab asks for a password, so a refusal that wants
 * one sends the person there (review of #659); any other says the server's words. */
export async function undoLink(id: string): Promise<unknown> {
  try {
    return await undoLinkVersions(id);
  } catch (caught) {
    if (needsPassword(caught)) throw new ApiError(403, "password_required", LINK_TEXT.undoInTab);
    throw caught;
  }
}

/** A file left out of a link, asked "Not a version of these?" on the Possible versions page. */
export interface Asked {
  groupId: string;
  file: { id: string; name: string };
}

/** The files asked about with `added` after them, each file once (reviews of #659: a second link
 * adds to the question, never replaces it). */
export function withAsked(before: readonly Asked[], added: readonly Asked[]): Asked[] {
  const asked = [...before];
  for (const each of added) if (!asked.some((one) => one.file.id === each.file.id)) asked.push(each);
  return asked;
}

/** The files asked about without these (answered, or their link undone). */
export function withoutAsked(before: readonly Asked[], ids: readonly string[]): Asked[] {
  return before.filter((one) => !ids.includes(one.file.id));
}

/** Whether a group, as a decision answered it, still shows this file (null: no group shown). */
export const shownIn = (group: VersionGroup | null, id: string) => group?.files.some((file) => file.id === id) === true;
