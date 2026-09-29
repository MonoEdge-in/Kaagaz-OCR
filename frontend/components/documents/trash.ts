// Trash: who may do what, and the words for it (PRD 1, F5). Moving to Trash,
// restoring and deleting for good all act on the whole family: every version
// of the document. Pure functions, unit tested in node (tests/trash.test.ts).
// The server checks every rule again.

import { sessionScope } from "@/components/shell/gate";
import type { DocumentSummary, Me } from "@/lib/types";

/**
 * Only an admin session (from /admin/login) may delete for good; the server
 * says 403 admin_session_required to anyone else. Move to Trash and Restore
 * follow each document's can_delete.
 */
export function canDeleteForever(me: Me): boolean {
  return me.user.role === "admin" && sessionScope(me) === "admin";
}

/** An admin signed in at /login: Trash says where Delete forever is. */
export function foreverNeedsAdminSignIn(me: Me): boolean {
  return me.user.role === "admin" && sessionScope(me) !== "admin";
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
