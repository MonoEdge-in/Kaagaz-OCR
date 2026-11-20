// Global search by name, path or link (#730), kept out of the screen so it is unit tested in node
// (tests/search-path-730.test.ts). What was typed or pasted is read as a Kaagaz file link (any
// host, or /documents/<id>), else as a path in any common form: Kaagaz's own "A › B › file.pdf",
// "A > B", "A/B", "A\B", a Windows "Copy as path" ("C:\...\file.pdf" in quotes, D:\, \\server\share),
// a Mac path (/Users/...) or a file:/// address. The computer-only folders at its start
// (Users, Documents, Desktop...) are dropped from the left until the rest matches what this
// person can see in Kaagaz: the longest tail that matches wins. Matching is any case, spaces
// trimmed, each part held in a folder's or file's name, in order, the last part in the item's
// own name (its extension optional). The folders are the tree GET /folders gives this person,
// so a folder they can't see is never named, and never matched.

import { folderPath, inTreeOrder } from "@/components/documents/folderTree";
import type { FolderTree } from "@/components/documents/folderTree";
import { folderPathText } from "@/components/chat/scope";
import type { DocumentSummary } from "@/lib/types";

/** The one line for nothing found, or nothing this person may open: never a name. */
export const NO_MATCH_TEXT =
  "No file or folder matches this, or you don't have access to it. Ask an Admin if you need it.";
export const FILES_SHOWN = 25;
export const FOLDERS_SHOWN = 10;
/** Files asked for by the last part's name, then matched on their path here.
 * shortcut: one page of name matches; a name shared by more files than this may miss the right
 * folder's file. Ask GET /documents per matched folder when that bites. */
export const FILES_FETCHED = 50;

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const FILE_LINK = new RegExp(`^(?:https?://[^/\\s]+)?/documents/(${UUID})(?:[/?#]\\S*)?$`, "i");

/** A Kaagaz file's link (its document page, on any host, or relative): its id, else null. */
export function documentFromLink(text: string): string | null {
  const found = FILE_LINK.exec(text.trim());
  return found ? found[1]!.toLowerCase() : null;
}

/** The parts of a pasted or typed path, root first: quotes, file://, a drive letter and a
 * \\server\share left out, then split on \ / › and >. */
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
  path = path.replace(/^\\\\[^\\/]+[\\/][^\\/]+/, ""); // \\server\share
  path = path.replace(/^[\\/]?[a-z]:(?=[\\/]|$)/i, ""); // C:, or /C: in a file:/// address
  return path
    .split(/[\\/›>]+/)
    .map((part) => part.replace(/\s+/g, " ").trim())
    .filter(Boolean);
}

const fold = (text: string) => text.replace(/\s+/g, " ").trim().toLocaleLowerCase();
const withoutExtension = (name: string) => name.replace(/\.[a-z0-9]{1,5}$/i, "");

/** What GET /documents?q= is asked for: the last part, its extension left out. */
export const fileQuery = (parts: readonly string[]) => withoutExtension(parts[parts.length - 1] ?? "");

/** Whether an item whose place is `names` (root first, itself last) matches `parts`: the last part
 * in its own name (extension optional), each other part in one of its folders, in order. */
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

function foldersMatching(tree: FolderTree, parts: readonly string[]): FoundPlace[] {
  return inTreeOrder(tree)
    .filter((node) => inPath(folderPath(tree, node.folder.id).map((folder) => folder.name), parts))
    .map((node) => ({ id: node.folder.id, path: folderPathText(tree, node.folder.id) }));
}

/** The files (of `named`, GET /documents?q=fileQuery) and folders the longest matching tail of
 * `parts` finds; else the folder its leading parts name, for its closest files. O(t * (n + f * d)). */
export function findByPath(tree: FolderTree, parts: readonly string[], named: readonly DocumentSummary[]): PathFound {
  for (let start = 0; start < parts.length; start += 1) {
    const tail = parts.slice(start);
    const files = named.filter((file) => inPath(fileNames(tree, file), tail));
    const folders = foldersMatching(tree, tail);
    if (files.length > 0 || folders.length > 0) return { files, folders, closestIn: null };
  }
  for (let start = 0; start < parts.length - 1; start += 1) {
    const [folder] = foldersMatching(tree, parts.slice(start, -1));
    if (folder) return { files: [], folders: [], closestIn: folder };
  }
  return { files: [], folders: [], closestIn: null };
}
