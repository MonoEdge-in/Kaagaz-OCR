// Global search by name, path or link (#730), kept out of the screen so it is unit tested in node
// (tests/search-screen.test.ts). What was typed or pasted is read as a Kaagaz file link (any
// host, or /documents/<id>), else as a path in any common form: Kaagaz's own "A › B › file.pdf"
// (split on "›" alone, so a name holding "/" or ">" survives Copy path), "A > B", "A/B", "A\B", a
// Windows "Copy as path" ("C:\...\file.pdf" in quotes, D:\, \\server\share), a Mac path (/Users/...)
// or a file:/// address. The parts at its start that Kaagaz doesn't have (a drive, a server, Users,
// Documents, Desktop...) are dropped from the left until the rest matches what this person can see:
// the longest tail that matches wins. Matching is any case, spaces folded, each part held in a
// folder's or file's name, in order, the last part in the item's own name (a real file type's
// extension optional). The folders are the tree GET /folders gives this person, so a folder they
// can't see is never named, and never matched.

import { folderPath, inTreeOrder } from "@/components/documents/folderTree";
import type { FolderTree } from "@/components/documents/folderTree";
import { ACCEPTED_EXTENSIONS } from "@/components/documents/logic";
import { MAX_SEARCH_LENGTH } from "@/components/documents/insideSearch";
import { folderPathText } from "@/components/chat/scope";
import type { DocumentSummary } from "@/lib/types";

/** The one line for nothing found, or nothing this person may open: never a name. */
export const NO_MATCH_TEXT =
  "No file or folder matches this, or you don't have access to it. Ask an Admin if you need it.";
export const FILES_SHOWN = 25;
export const FOLDERS_SHOWN = 10;
/** Files asked for by the last part's name, then matched on their path here; when more files hold
 * that name, asked again in each of the first PATH_FOLDERS folders the path names. */
export const FILES_FETCHED = 50;
export const PATH_FOLDERS = 3;
/** The longest text the global box takes: a pasted Windows path (260) or a file:/// address whose
 * names are URL-encoded Devanagari (about 9 characters a letter). Only the name asked of the server
 * is cut to MAX_SEARCH_LENGTH. */
export const MAX_FIND_LENGTH = 1000;

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const FILE_LINK = new RegExp(`^(?:https?://[^/\\s]+)?/documents/(${UUID})(?:[/?#]\\S*)?$`, "i");

/** A Kaagaz file's link (its document page, on any host, or relative): its id, else null. */
export function documentFromLink(text: string): string | null {
  const found = FILE_LINK.exec(text.trim());
  return found ? found[1]!.toLowerCase() : null;
}

/** The parts of a pasted or typed path, root first: quotes and file:// left out (the address
 * decoded); split on "›" alone when it holds one (Copy path's), else on \ / and >. Each part is
 * trimmed, its inner spaces kept as typed (``fold`` folds them when comparing). */
export function pathParts(text: string): string[] {
  let path = text.trim().replace(/^["']+|["']+$/g, "");
  if (/^file:\/\//i.test(path)) {
    path = path.replace(/^file:\/\/(?:localhost)?/i, "");
    try {
      path = decodeURIComponent(path);
    } catch {
      // a stray "%" is kept as typed
    }
  }
  return path
    .split(path.includes("›") ? /›+/ : /[\\/>]+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

const fold = (text: string) => text.replace(/\s+/g, " ").trim().toLocaleLowerCase();
const FILE_TYPE = new RegExp(`\\.(?:${ACCEPTED_EXTENSIONS.join("|")})$`, "i");
/** A name without a real file type's extension: "Gat No.44" keeps its ".44". */
const withoutExtension = (name: string) => name.replace(FILE_TYPE, "");

/** What GET /documents?q= is asked for: the last part, a file type's extension left out, at most
 * MAX_SEARCH_LENGTH characters (the server's cap). */
export const fileQuery = (parts: readonly string[]) =>
  withoutExtension(parts[parts.length - 1] ?? "").slice(0, MAX_SEARCH_LENGTH);

/** Whether an item whose place is `names` (root first, itself last) matches `parts`: the last part
 * in its own name (a file type's extension optional), each other part in one of its folders, in order. */
export function inPath(names: readonly string[], parts: readonly string[]): boolean {
  if (names.length === 0 || parts.length === 0) return false;
  const held = names.map(fold);
  const own = held[held.length - 1]!;
  const last = fold(parts[parts.length - 1]!);
  if (!own.includes(last) && !own.includes(withoutExtension(last))) return false;
  let at = 0;
  for (const part of parts.slice(0, -1).map(fold)) {
    while (at < held.length - 1 && !held[at]!.includes(part)) at += 1;
    if (at >= held.length - 1) return false;
    at += 1;
  }
  return true;
}

/** A file's place, root first, itself last: only folders this person can see. */
export function fileNames(tree: FolderTree, file: Pick<DocumentSummary, "folder_id" | "display_name">): string[] {
  const folders = file.folder_id && tree.byId.has(file.folder_id) ? folderPath(tree, file.folder_id) : [];
  return [...folders.map((folder) => folder.name), file.display_name];
}

export interface FoundPlace {
  id: string;
  path: string; // "Land & Legal › Land records"
}

export interface PathFound {
  files: DocumentSummary[];
  folders: FoundPlace[];
  /** No file matched, but this folder did (the path's folders, without its last part): its files are
   * the closest, asked for apart. */
  closestIn: FoundPlace | null;
}

export function foldersMatching(tree: FolderTree, parts: readonly string[]): FoundPlace[] {
  return inTreeOrder(tree)
    .filter((node) => inPath(folderPath(tree, node.folder.id).map((folder) => folder.name), parts))
    .map((node) => ({ id: node.folder.id, path: folderPathText(tree, node.folder.id) }));
}

/** The folders a path's leading parts name, by their longest matching tail: where to ask again for
 * a file whose name more files hold than one page. [] for a name alone. */
export function pathFolders(tree: FolderTree, parts: readonly string[]): FoundPlace[] {
  for (let start = 0; start < parts.length - 1; start += 1) {
    const folders = foldersMatching(tree, parts.slice(start, -1));
    if (folders.length > 0) return folders;
  }
  return [];
}

/** The files (of `named`, GET /documents?q=fileQuery) and folders the longest matching tail of
 * `parts` finds, a file whose name holds the whole last part first; else the folder its leading
 * parts name (`closestIn`, also listed), for its closest files. O(t * (n + f * d)). */
export function findByPath(tree: FolderTree, parts: readonly string[], named: readonly DocumentSummary[]): PathFound {
  const whole = fold(parts[parts.length - 1] ?? "");
  const holdsWhole = (file: DocumentSummary) => fold(file.display_name).includes(whole);
  for (let start = 0; start < parts.length; start += 1) {
    const tail = parts.slice(start);
    const matched = named.filter((file) => inPath(fileNames(tree, file), tail));
    const files = [...matched.filter(holdsWhole), ...matched.filter((file) => !holdsWhole(file))];
    const folders = foldersMatching(tree, tail);
    if (files.length > 0 || folders.length > 0) return { files, folders, closestIn: null };
  }
  const [folder] = pathFolders(tree, parts);
  return folder ? { files: [], folders: [folder], closestIn: folder } : { files: [], folders: [], closestIn: null };
}
