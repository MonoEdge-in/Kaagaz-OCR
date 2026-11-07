// Trash: who may do what, and the words for it (PRD 1, F5). Moving to Trash,
// restoring and deleting for good all act on the whole family: every version
// of the document. Pure functions, unit tested in node (tests/trash.test.ts).
// The server checks every rule again.

import { sessionScope } from "@/components/shell/gate";
import { ApiError, deleteDocument, restoreDocument } from "@/lib/api";
import type { DocumentSummary, FolderBack, FolderRestored, Me, TrashedFolder } from "@/lib/types";

/**
 * Who may delete for good: an admin session (every admin's, from /login), or a member or
 * reviewer an admin allowed (can_delete_forever). An admin without two-step sign-in on (#538)
 * and a granted user type their password first (foreverNeedsPassword). The server says 403
 * to anyone else. Move to Trash and Restore follow each document's can_delete.
 */
export function canDeleteForever(me: Me): boolean {
  if (me.user.role !== "admin") return me.user.can_delete_forever;
  return sessionScope(me) === "admin";
}

/** An admin without two-step sign-in on (#538), or a granted member or reviewer, types their
 * password before deleting for good. */
export function foreverNeedsPassword(me: Me): boolean {
  if (me.user.role !== "admin") return me.user.can_delete_forever;
  return sessionScope(me) === "admin" && !me.user.totp_enabled;
}

/** What must happen first to delete forever, for the note in Trash; null if nothing. */
export function foreverNeeds(me: Me): "admin_sign_in" | "not_allowed" | null {
  if (me.user.role !== "admin") return me.user.can_delete_forever ? null : "not_allowed";
  return sessionScope(me) === "admin" ? null : "admin_sign_in";
}

/** A row can be ticked: to move to Trash or restore, or in Trash to delete for good. */
export function selectable(item: Pick<DocumentSummary, "can_delete">, inTrash: boolean, canForever: boolean) {
  return item.can_delete || (inTrash && canForever);
}

/** 403 forbidden: an admin stopped this user's Delete forever; ask who is signed in again. */
export function lostTheRight(caught: unknown): boolean {
  return caught instanceof ApiError && caught.code === "forbidden";
}

/** 403 password_confirm_required: no typed password counts now (it is 5 minutes old, or none was
 * typed); the question stays open and asks for it again (#538). */
export function passwordAgain(caught: unknown): boolean {
  return caught instanceof ApiError && caught.code === "password_confirm_required";
}

export type ForeverOutcome =
  | { kind: "password"; error: unknown } // the password was refused: nothing was deleted
  | { kind: "ran"; result: BulkResult };

/** runEach, after checking a granted user's password once (a 5-minute cookie); a refused one deletes nothing. */
export async function runForever(
  ids: readonly string[],
  password: string | null,
  calls: { confirm(password: string): Promise<unknown>; remove(id: string): Promise<unknown> },
  onProgress?: (handled: number) => void,
): Promise<ForeverOutcome> {
  if (password !== null) {
    try {
      await calls.confirm(password);
    } catch (caught) {
      return { kind: "password", error: caught };
    }
  }
  return { kind: "ran", result: await runEach(ids, calls.remove, onProgress) };
}

/** "1 document", "3 documents". */
export function documentsCount(count: number): string {
  return `${count} ${count === 1 ? "document" : "documents"}`;
}

const counted = (count: number, one: string) => `${count} ${one}${count === 1 ? "" : "s"}`;

/** #542: a selection of documents and folders: "3 documents", "1 folder", "2 documents and 1 folder". */
export function itemsCount(documents: number, folders = 0): string {
  if (folders === 0) return documentsCount(documents);
  if (documents === 0) return counted(folders, "folder");
  return `${documentsCount(documents)} and ${counted(folders, "folder")}`;
}

// --- A folder in Trash (#542): one item, opened read only, restored where it was.

/** "Was in: Accounts › 2025", "Was in: Top level", or where it was is gone. */
export function wasInText(wasIn: TrashedFolder["was_in"]): string {
  if (wasIn === null) return "Was in: a folder that no longer exists";
  return `Was in: ${wasIn.length === 0 ? "Top level" : wasIn.join(" › ")}`;
}

/** "4 files, 3 folders"; a part it has none of is left out ("4 files"), and nothing at all is "Empty". */
export function heldText(folder: Pick<TrashedFolder, "files" | "folders">): string {
  const parts = [folder.files > 0 ? counted(folder.files, "file") : "", folder.folders > 0 ? counted(folder.folders, "folder") : ""];
  return parts.filter(Boolean).join(", ") || "Empty";
}

/** The toast once a folder is back: where it went, or why it is not where it was. */
export function folderRestoredMessage(restored: Pick<FolderRestored, "folder" | "location" | "renamed" | "relocated">): string {
  const said: string[] = [];
  if (restored.relocated) said.push("Restored to Top level, because its folder no longer exists.");
  if (restored.renamed) said.push(`Restored as “${restored.folder.name}”, because a folder with that name already exists.`);
  if (said.length > 0) return said.join(" ");
  return `Folder restored to ${restored.location.length === 0 ? "Top level" : restored.location.join(" › ")}.`;
}

/** A file restored from a folder in Trash brought its folder back: where, or why not where it was. */
export function folderBackMessage(back: FolderBack): string {
  if (back.relocated) return "Its folder is back at Top level, because the folder it was in no longer exists.";
  if (back.renamed) return `Its folder is back as “${back.folder.name}”, because a folder with that name already exists.`;
  return `Its folder “${back.folder.name}” is back in ${back.location.length === 0 ? "Top level" : back.location.join(" › ")}.`;
}

/** Asked before a folder in Trash is deleted for good, with every file in it, in foreverQuestion's words. */
export function folderForeverQuestion(folder: Pick<TrashedFolder, "name" | "files">): string {
  if (folder.files === 0) return `Delete “${folder.name}” for good? It can't be brought back.`; // no zero count
  const gone = folder.files === 1 ? "It can't be brought back." : "They can't be brought back.";
  return `Delete “${folder.name}” and its ${counted(folder.files, "file")} for good? ${gone}`;
}

/** B18: a folder deleted for good a part at a time stopped part way; why follows. */
export function foreverStoppedText(left: number): string {
  return `Deleting stopped with ${counted(left, "file")} still in Trash.`;
}

/** Trash with nothing in it, or an open folder in Trash with nothing left in it. */
export function trashEmpty(inFolder: boolean): { title: string; text: string } {
  return inFolder
    ? { title: "This folder is empty", text: "Nothing was in it when it was deleted, or what was is back in Documents." }
    : {
        title: "Trash is empty",
        text: "Documents and folders moved to Trash show here, until they are restored or deleted for good.",
      };
}

/** A selection's key for a folder in Trash: one set holds documents and folders (#542). */
export const folderKey = (id: string) => `folder:${id}`;
export const isFolderKey = (key: string) => key.startsWith("folder:");
export const folderOf = (key: string) => key.slice("folder:".length);

/** The document by name, with what one action takes with it: “Bill.pdf”, or “Bill.pdf” and its 2
 * earlier versions. The name is said, so a question about a destructive step names its document. */
function withVersions(document: Pick<DocumentSummary, "version_no" | "display_name">): string {
  const name = `“${document.display_name}”`;
  const earlier = document.version_no - 1;
  if (earlier <= 0) return name;
  return earlier === 1 ? `${name} and its earlier version` : `${name} and its ${earlier} earlier versions`;
}

export function trashQuestion(document: Pick<DocumentSummary, "version_no" | "display_name">): string {
  return `Move ${withVersions(document)} to Trash?`;
}

export function bulkTrashQuestion(count: number): string {
  return `Move ${count} selected ${count === 1 ? "document" : "documents"} to Trash?`;
}

export function foreverQuestion(document: Pick<DocumentSummary, "version_no" | "display_name">): string {
  const gone = document.version_no > 1 ? "They can't be brought back." : "It can't be brought back.";
  return `Delete ${withVersions(document)} for good? ${gone}`;
}

export function restoredMessage(name: string): string {
  return `Restored ${name}.`;
}

export function deletedForeverMessage(name: string): string {
  return `Deleted ${name} for good.`;
}

// --- Acting on a selection: Move to Trash in the list, Restore and Delete forever in Trash.

/** What a selection can be asked to do. */
// "read": Read again (PRD 5 U2), for a reviewer or an admin, one document at a time (POST /reprocess).
export type BulkAction = "trash" | "restore" | "forever" | "read";

/** Asked before a selection is read again: the same warning as one document's Read again. */
export function bulkReadQuestion(count: number): string {
  return `Read ${documentsCount(count)} again? Reviewer fixes come back only if they were saved as remembered fixes, and each one is out of search until it is read.`;
}

export function bulkForeverQuestion(count: number, folders = 0): string {
  if (folders > 0) {
    return `Delete ${itemsCount(count, folders)} for good, with every file and version in them? They can't be brought back.`;
  }
  return count === 1
    ? "Delete 1 selected document for good, with all its versions? It can't be brought back."
    : `Delete ${count} selected documents for good, with all their versions? They can't be brought back.`;
}

/** "Restored 3 documents.": what the selection had done to it; #542: "and 1 folder". */
export function bulkDoneMessage(action: BulkAction, count: number, folders = 0): string {
  const documents = itemsCount(count, folders);
  if (action === "restore") return `Restored ${documents}.`;
  if (action === "forever") return `Deleted ${documents} for good.`;
  if (action === "read") return `Reading ${documents} again.`;
  return `Moved ${documents} to Trash.`;
}

const FAILED_TO: Record<BulkAction, string> = {
  trash: "moved to Trash",
  restore: "restored",
  forever: "deleted",
  read: "read again",
};

/** Undo for a removal, with the words said once it is undone. */
export interface RemovalUndo {
  run(): Promise<void>;
  done: string;
}

/**
 * Undo for Move to Trash (restore them) and Restore (move them back to Trash), as Move to
 * folder has (issue 372); none for Delete forever, which cannot be undone, or Read again.
 * The documents go back one at a time, as the selection's own actions do.
 */
export function undoRemoval(
  action: BulkAction,
  ids: readonly string[],
  calls: { trash(id: string): Promise<unknown>; restore(id: string): Promise<unknown> } = {
    trash: deleteDocument,
    restore: restoreDocument,
  },
): RemovalUndo | undefined {
  if (ids.length === 0 || (action !== "trash" && action !== "restore")) return undefined;
  const back = action === "trash" ? calls.restore : calls.trash;
  return {
    run: async () => {
      for (const id of ids) await back(id);
    },
    done: action === "trash" ? "Restored from Trash." : "Moved back to Trash.",
  };
}

/** "2 documents could not be restored. <why>"; #542: "and 1 folder". */
export function bulkFailedMessage(action: BulkAction, failed: number, reason: string, folders = 0): string {
  return `${itemsCount(failed, folders)} could not be ${FAILED_TO[action]}. ${reason}`;
}

export interface BulkResult {
  done: string[];
  failed: string[];
  /** The first failure, for its message; undefined if none failed. */
  firstError: unknown;
  /** The last failure: a password that ran out partway fails every one after it (#538). */
  lastError: unknown;
}

/**
 * Run call for each id, one at a time and in order, and keep going past a
 * failure, so one document the server refuses doesn't hold back the rest.
 * onProgress gets how many have been handled so far. O(n) calls, one in flight.
 */
export async function runEach(
  ids: readonly string[],
  call: (id: string) => Promise<unknown>,
  onProgress?: (handled: number) => void,
): Promise<BulkResult> {
  const result: BulkResult = { done: [], failed: [], firstError: undefined, lastError: undefined };
  for (const [index, id] of ids.entries()) {
    try {
      await call(id);
      result.done.push(id);
    } catch (caught) {
      if (result.failed.length === 0) result.firstError = caught;
      result.lastError = caught;
      result.failed.push(id);
    }
    onProgress?.(index + 1);
  }
  return result;
}
