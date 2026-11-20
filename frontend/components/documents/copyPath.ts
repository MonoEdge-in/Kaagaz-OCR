// "Copy path" and "Copy link" (#730): what a file's or folder's menu copies, and the line that says
// how to use it. A path is the folders this person can see, root first, then the file's name,
// joined with " › " (as Search reads it back, components/search/pathSearch.ts); a file in a folder
// they can't see is "Shared with you › <name>", never a hidden folder's name. A link is the file's
// document page, in full. The clipboard is the browser's, as components/admin/CopyButton.tsx
// uses it; when it refuses, the person is told, never left guessing.

import { SHARED } from "@/components/access/words";
import { fileNames } from "@/components/search/pathSearch";
import type { FolderTree } from "./folderTree";
import type { DocumentDetail, DocumentSummary } from "@/lib/types";

export const PATH_COPIED = "Path copied. Paste it in Search to find this file.";
export const FOLDER_PATH_COPIED = "Path copied. Paste it in Search to find this folder.";
export const LINK_COPIED = "Link copied. Anyone who can open this file can use it.";
/** The browser refused: the text itself, to select and copy by hand. */
export const copyFailed = (text: string) => `Could not copy. Select it here: ${text}`;

/** A listed file's path, from the folders this person sees (`tree`). */
export function filePath(
  tree: FolderTree,
  file: Pick<DocumentSummary, "folder_id" | "display_name" | "shared_with_you">,
): string {
  if (file.shared_with_you) return `${SHARED.row} › ${file.display_name}`;
  return fileNames(tree, file).join(" › ");
}

/** The document screen's file's path, from its own folder trail (null when one is hidden). */
export function detailPath(detail: Pick<DocumentDetail, "folder" | "display_name" | "shared_with_you">): string {
  if (detail.shared_with_you) return `${SHARED.row} › ${detail.display_name}`;
  return [...(detail.folder?.path ?? []).map((folder) => folder.name), detail.display_name].join(" › ");
}

/** The file's document page, in full: a link that works pasted anywhere. */
export function fileLink(documentId: string, origin: string = window.location.origin): string {
  return `${origin}/documents/${encodeURIComponent(documentId)}`;
}

/** Puts `text` on the clipboard: true when it is there, false when the browser refused. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false; // no permission, not a secure page, or no clipboard at all: the caller says so
  }
}

/** Copies a file's path or link: whether it worked, and the line to show: what to do with it, or
 * the text to select by hand. */
export async function copyFor(
  action: "copyPath" | "copyLink",
  path: string,
  documentId: string,
): Promise<{ copied: boolean; line: string }> {
  const text = action === "copyPath" ? path : fileLink(documentId);
  if (!(await copyText(text))) return { copied: false, line: copyFailed(text) };
  return { copied: true, line: action === "copyPath" ? PATH_COPIED : LINK_COPIED };
}
