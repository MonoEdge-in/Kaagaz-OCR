// Small decisions for folders, tags, rename and download, shared by the
// documents screen and the viewer header. Pure functions, unit tested in node
// (tests/shell-documents.test.ts). The server checks everything again.

import { ApiError, fileUrl } from "@/lib/api";
import { formatDate, typeLabel } from "@/lib/format";
import type {
  DetectedType,
  DocumentFolder,
  DocumentStatus,
  DocumentSummary,
  Tag,
} from "@/lib/types";

// The original is served only once the virus check has passed
// (backend/kaagaz/api/routers/documents.py, _SERVABLE).
const SERVABLE: readonly DocumentStatus[] = ["clean", "ready", "needs_reader"];

/** Where "Download original" points: always through the API. null when the server would refuse. */
export function downloadHref(document: Pick<DocumentSummary, "id" | "status">): string | null {
  return SERVABLE.includes(document.status) ? fileUrl(document.id) : null;
}

export type EditAction = "rename" | "move" | "tags";
export type RowAction = EditAction | "version" | "trash" | "restore" | "forever" | "read";

export type MenuEntry =
  | { kind: "action"; action: RowAction; words: string; danger: boolean }
  | { kind: "download"; href: string };

/**
 * What a document's Actions menu lists, in order: the everyday actions,
 * Download original, then the one that removes. A document in Trash can't be
 * renamed, moved, tagged or given a new version (the server says 409
 * in_trash): it offers Restore when can_delete, and Delete forever in an
 * admin session (canForever). Out of Trash, Move to Trash needs can_delete.
 * On the document screen (UI audit DD2, 16 Oct 2026) the menu starts with Read again
 * (`readAgain`: canReprocess) and leaves out Download original (a button beside it) and
 * Upload new version (the reading problems' Upload a better scan).
 */
export function menuEntries(
  item: Pick<DocumentSummary, "id" | "status" | "deleted_at" | "can_delete">,
  canForever: boolean,
  place: "list" | "document" = "list",
  readAgain = false,
): MenuEntry[] {
  const act = (action: RowAction, words: string, danger = false): MenuEntry => ({
    kind: "action",
    action,
    words,
    danger,
  });
  const trashed = item.deleted_at !== null;
  const entries: MenuEntry[] = trashed
    ? item.can_delete
      ? [act("restore", "Restore")]
      : []
    : [
        act("rename", "Rename"),
        act("move", "Move to folder"),
        act("tags", "Tags"),
        act("version", "Upload new version"),
      ];
  if (place === "document") {
    const kept = entries.filter((entry) => entry.kind === "action" && entry.action !== "version");
    if (readAgain && !trashed) kept.unshift(act("read", "Read again"));
    if (!trashed && item.can_delete) kept.push(act("trash", "Move to Trash", true));
    return kept;
  }
  // Trash means deleted: a document there can only be restored or deleted forever.
  const download = trashed ? null : downloadHref(item);
  if (download) entries.push({ kind: "download", href: download });
  if (trashed && canForever) entries.push(act("forever", "Delete forever", true));
  if (!trashed && item.can_delete) entries.push(act("trash", "Move to Trash", true));
  return entries;
}

/**
 * Where an open menu goes (D5): under its button, or above it when there is not room
 * below and more above; its end edge lined up with the button's, kept 8px inside the
 * window. Fixed coordinates (the menu is in a portal, so <main> never cuts it).
 */
export function menuPlace(
  button: { top: number; bottom: number; right: number },
  menu: { width: number; height: number },
  view: { width: number; height: number },
): { top: number; left: number; up: boolean } {
  const gap = 4;
  const edge = 8;
  const below = view.height - button.bottom - gap - edge;
  const above = button.top - gap - edge;
  const up = menu.height > below && above > below;
  const top = up ? Math.max(edge, button.top - gap - menu.height) : button.bottom + gap;
  const left = Math.min(Math.max(edge, button.right - menu.width), Math.max(edge, view.width - menu.width - edge));
  return { top, left, up };
}

/** The menu item focused after a key (Up/Down wrap, Home/End the ends); null for other keys. */
export function menuItemAfterKey(index: number, key: string, count: number): number | null {
  if (count === 0) return null;
  if (key === "ArrowDown") return (index + 1) % count;
  if (key === "ArrowUp") return (index - 1 + count) % count;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return null;
}

/** The folders from the top down to the document's own, whether or not `path` already ends with it. */
export function folderTrail(folder: DocumentFolder): { id: string; name: string }[] {
  const last = folder.path.at(-1);
  return last?.id === folder.id
    ? folder.path
    : [...folder.path, { id: folder.id, name: folder.name }];
}

function nameProblem(name: string, what: string, max: number): string | null {
  const trimmed = name.trim();
  if (trimmed === "") return `Enter a name for the ${what}.`;
  if (trimmed.length > max) return `Use at most ${max} characters.`;
  return null;
}

/** Folder names are 1 to 100 characters (PRD 1, F1). */
export const folderNameProblem = (name: string) => nameProblem(name, "folder", 100);
/** Tag names are 1 to 40 characters (PRD 1, F2). */
export const tagNameProblem = (name: string) => nameProblem(name, "tag", 40);
/** Document names are 1 to 255 characters; "GST 2026/27" keeps its slash. */
export const documentNameProblem = (name: string) => nameProblem(name, "document", 255);

/** The tag already called this, whatever the letter case, or null. Tag names are unique that way. */
export function findTagByName(tags: readonly Tag[], name: string): Tag | null {
  const wanted = name.trim().toLowerCase();
  return tags.find((tag) => tag.name.toLowerCase() === wanted) ?? null;
}

/**
 * What to say next to the form when a change fails. A taken name gets its own
 * words; the folder rules (folder_cycle, folder_too_deep, folder_not_empty)
 * come with the server's own message.
 */
export function libraryErrorText(error: unknown, what: "folder" | "tag" | "document"): string {
  if (!(error instanceof ApiError)) return "Check your internet connection, then try again.";
  if (error.code === "conflict" && what === "folder") {
    return "A folder with this name is already here. Choose another name.";
  }
  if (error.code === "conflict" && what === "tag") return "A tag with this name already exists.";
  return error.message;
}

// The words on each filter chip say what it is set to, so the chip row alone
// tells what the list shows.

export function tagsChipText(names: readonly string[]): string {
  const [first] = names;
  if (first === undefined) return "Tags";
  return names.length === 1 ? `Tag: ${first}` : `Tags: ${first} and ${names.length - 1} more`;
}

export function typeChipText(type: DetectedType | null): string {
  return type === null ? "Type" : `Type: ${typeLabel(type)}`;
}

/** from and to are YYYY-MM-DD, both days included. */
export function dateChipText(from: string | null, to: string | null): string {
  if (from !== null && to !== null) return `Uploaded ${formatDate(from)} to ${formatDate(to)}`;
  if (from !== null) return `Uploaded from ${formatDate(from)}`;
  if (to !== null) return `Uploaded until ${formatDate(to)}`;
  return "Date uploaded";
}

/** The heading when the search or the filters leave nothing to list. */
export function noMatchTitle(q: string, filtered: boolean, attentionOnly = false): string {
  if (q === "" && attentionOnly) return "Nothing needs attention.";
  if (q === "") return "No documents match these filters.";
  return filtered
    ? `No document names contain “${q}” with these filters.`
    : `No document names contain “${q}”.`;
}

const counted = (n: number, one: string, many: string) => `${n.toLocaleString("en-IN")} ${n === 1 ? one : many}`;

/**
 * The chips on a document with reading problems (PRD 5 U2): "2 pages to check",
 * "14 words unsure", "3 values to check"; [] when it has none. Calm words, never "unreadable"
 * (Tushar, 16 Oct 2026): unreadable pages, and uncertain ones with no word marked unsure,
 * are counted together as pages to check.
 */
export function attentionWords(
  document: Pick<DocumentSummary, "pages_unreadable" | "pages_uncertain" | "values_to_review" | "words_unsure">,
): string[] {
  const unsure = document.words_unsure ?? 0;
  const pages = document.pages_unreadable + (unsure === 0 ? document.pages_uncertain : 0);
  return [
    pages > 0 ? counted(pages, "page to check", "pages to check") : "",
    unsure > 0 ? counted(unsure, "word unsure", "words unsure") : "",
    document.values_to_review > 0 ? counted(document.values_to_review, "value to check", "values to check") : "",
  ].filter(Boolean);
}
