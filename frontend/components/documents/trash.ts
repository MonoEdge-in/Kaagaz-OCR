// Trash: who may do what, and the words for it (PRD 1, F5). Moving to Trash,
// restoring and deleting for good all act on the whole family: every version
// of the document. Pure functions, unit tested in node (tests/trash.test.ts).
// The server checks every rule again.

import { sessionScope } from "@/components/shell/gate";
import { ApiError } from "@/lib/api";
import type { DocumentSummary, Me } from "@/lib/types";

/**
 * Who may delete for good: an admin session (from /admin/login) whose admin has
 * two-step sign-in on, or a member or reviewer an admin allowed
 * (can_delete_forever), who types their password first. The server says 403 to
 * anyone else. Move to Trash and Restore follow each document's can_delete.
 */
export function canDeleteForever(me: Me): boolean {
  if (me.user.role !== "admin") return me.user.can_delete_forever;
  return sessionScope(me) === "admin" && me.user.totp_enabled;
}

/** A granted member or reviewer types their password before deleting for good. */
export function foreverNeedsPassword(me: Me): boolean {
  return me.user.role !== "admin" && me.user.can_delete_forever;
}

/** What must happen first to delete forever, for the note in Trash; null if nothing. */
export function foreverNeeds(me: Me): "admin_sign_in" | "two_step" | "not_allowed" | null {
  if (me.user.role !== "admin") return me.user.can_delete_forever ? null : "not_allowed";
  if (sessionScope(me) !== "admin") return "admin_sign_in";
  return me.user.totp_enabled ? null : "two_step";
}

/** A row can be ticked: to move to Trash or restore, or in Trash to delete for good. */
export function selectable(item: Pick<DocumentSummary, "can_delete">, inTrash: boolean, canForever: boolean) {
  return item.can_delete || (inTrash && canForever);
}

/** 403 forbidden: an admin stopped this user's Delete forever; ask who is signed in again. */
export function lostTheRight(caught: unknown): boolean {
  return caught instanceof ApiError && caught.code === "forbidden";
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

/** "the document", or "the document and its 2 earlier versions": what one action takes with it. */
function withVersions(document: Pick<DocumentSummary, "version_no">): string {
  const earlier = document.version_no - 1;
  if (earlier <= 0) return "this document";
  return earlier === 1
    ? "this document and its earlier version"
    : `this document and its ${earlier} earlier versions`;
}

export function trashQuestion(document: Pick<DocumentSummary, "version_no">): string {
  return `Move ${withVersions(document)} to Trash?`;
}

export function bulkTrashQuestion(count: number): string {
  return `Move ${count} selected ${count === 1 ? "document" : "documents"} to Trash?`;
}

export function foreverQuestion(document: Pick<DocumentSummary, "version_no">): string {
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
export type BulkAction = "trash" | "restore" | "forever";

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
  return `Moved ${documents} to Trash.`;
}

const FAILED_TO: Record<BulkAction, string> = {
  trash: "moved to Trash",
  restore: "restored",
  forever: "deleted",
};

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
