// Trash: who may do what, and the words for it (PRD 1, F5). Moving to Trash,
// restoring and deleting for good all act on the whole family: every version
// of the document. Pure functions, unit tested in node (tests/trash.test.ts).
// The server checks every rule again.

import { sessionScope } from "@/components/shell/gate";
import type { DocumentSummary, Me } from "@/lib/types";

/**
 * Only an admin session (from /admin/login) whose admin has two-step sign-in on
 * may delete for good; the server says 403 admin_session_required or
 * two_step_required to anyone else. Move to Trash and Restore follow each
 * document's can_delete.
 */
export function canDeleteForever(me: Me): boolean {
  return me.user.role === "admin" && sessionScope(me) === "admin" && me.user.totp_enabled;
}

/** What an admin must do first to delete forever, for the note in Trash; null if nothing. */
export function foreverNeeds(me: Me): "admin_sign_in" | "two_step" | null {
  if (me.user.role !== "admin") return null;
  if (sessionScope(me) !== "admin") return "admin_sign_in";
  return me.user.totp_enabled ? null : "two_step";
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
