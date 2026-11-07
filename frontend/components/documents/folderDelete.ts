// The words of Delete on a folder that holds files (#537, Tushar, 7 Nov 2026): what it holds,
// the Trash button, the toast after, and what a member or reviewer is told (DeleteFolderDialog,
// FolderActions). "Folder" or "department", as the folder is.

import { formatBytes, formatNumber } from "@/lib/format";
import type { FolderSummary } from "@/lib/types";

export type FolderNoun = "folder" | "department";

// shortcut: TRASH_DAYS's default (docs/running-locally.md); the browser is not told the setting.
export const RESTORE_DAYS = 30;

const count = (n: number, one: string) => `${formatNumber(n)} ${one}${n === 1 ? "" : "s"}`;
const capital = (noun: FolderNoun) => (noun === "folder" ? "Folder" : "Department");

export const isEmpty = (summary: FolderSummary) => summary.files === 0 && summary.folders === 0;

/** "It holds 3 files and 2 folders (1.5 KB)."; a part it has none of is left out. */
export function heldLine({ files, folders, bytes }: FolderSummary): string {
  const parts = [files > 0 ? count(files, "file") : "", folders > 0 ? count(folders, "folder") : ""].filter(Boolean);
  return `It holds ${parts.join(" and ")}${files > 0 ? ` (${formatBytes(bytes)})` : ""}.`;
}

/** The Trash choice's button: the files it moves, or just Delete when only empty folders are inside. */
export function trashButton(files: number, noun: FolderNoun): string {
  return files === 0 ? `Delete ${noun}` : `Move ${count(files, "file")} to Trash`;
}

/** The toast once what was inside has moved; `place` null is the top level. */
export function keptMessage(noun: FolderNoun, place: string | null, files: number): string {
  return `${capital(noun)} deleted. ${files > 0 ? "Its files" : "What was inside"} moved to ${place ?? "the top level"}.`;
}

/** The toast once the files went to Trash: as many as the server says it moved. */
export function trashedMessage(noun: FolderNoun, files: number): string {
  return files === 0 ? `${capital(noun)} deleted.` : `${capital(noun)} deleted. ${count(files, "file")} moved to Trash.`;
}

/** A member or reviewer, before any Delete: only an admin deletes a folder that holds files. */
export function notEmptyText(noun: FolderNoun): string {
  return `This ${noun} isn't empty. Ask an admin to delete it, or move what is inside first.`;
}
