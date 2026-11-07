// Trash: who may do what, and the words for it (PRD 1, F5). Moving to Trash,
// restoring and deleting for good all act on the whole family: every version
// of the document. Pure functions, unit tested in node (tests/trash.test.ts).
// The server checks every rule again.

import { sessionScope } from "@/components/shell/gate";
import { ApiError, deleteDocument, restoreDocument } from "@/lib/api";
import type { DocumentSummary, Me } from "@/lib/types";

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

export function bulkForeverQuestion(count: number): string {
  return count === 1
    ? "Delete 1 selected document for good, with all its versions? It can't be brought back."
    : `Delete ${count} selected documents for good, with all their versions? They can't be brought back.`;
}

/** "Restored 3 documents.": what the selection had done to it. */
export function bulkDoneMessage(action: BulkAction, count: number): string {
  const documents = documentsCount(count);
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

/** "2 documents could not be restored. <why>". */
export function bulkFailedMessage(action: BulkAction, failed: number, reason: string): string {
  return `${documentsCount(failed)} could not be ${FAILED_TO[action]}. ${reason}`;
}

export interface BulkResult {
  done: string[];
  failed: string[];
  /** The first failure, for its message; undefined if none failed. */
  firstError: unknown;
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
  const result: BulkResult = { done: [], failed: [], firstError: undefined };
  for (const [index, id] of ids.entries()) {
    try {
      await call(id);
      result.done.push(id);
    } catch (caught) {
      if (result.failed.length === 0) result.firstError = caught;
      result.failed.push(id);
    }
    onProgress?.(index + 1);
  }
  return result;
}
