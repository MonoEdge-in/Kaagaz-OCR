// Which documents the next question may use (PRD 3 H2), kept out of the
// components so it is unit tested in node (tests/chat-scope.test.ts): the
// choice as the body POST /api/chat takes, a saved body back into a choice
// (so a reopened conversation shows its chip again), the chip's words, and
// what the status line says aloud.
//
// A follow-up sends no scope and keeps the conversation's last one (plan
// decision 9, PROVISIONAL): the screen sends one only after the person changes
// it, or when chat was opened with "Ask about this document". The server
// resolves every id again for this customer.

import { inTreeOrder, pathLabel } from "@/components/documents/folderTree";
import type { FolderTree } from "@/components/documents/folderTree";
import type { Message, ScopeBody } from "@/lib/types";

export type ChatScope =
  | { kind: "all" }
  | { kind: "folder"; folderId: string; subfolders: boolean }
  | { kind: "documents"; ids: string[] }
  // Tags, types or dates: not offered here, so a saved one is kept as it was.
  | { kind: "other"; body: ScopeBody }
  // A conversation still loading (or that failed to load): the server keeps its saved scope.
  | { kind: "kept" };

/** A scope the person can pick, and the server can be sent. */
export type ChosenScope = Exclude<ChatScope, { kind: "kept" }>;

export const EVERY_DOCUMENT: ChosenScope = { kind: "all" };

/** What the chip and the picker show, and whether the next question sends it. */
export interface ScopeState {
  scope: ChatScope;
  changed: boolean; // true: send it with the next question; false: the conversation keeps its own
  documents: string[] | null; // the documents the picker offers, once there were some
  announce: boolean; // true after a pick or a restore: the status line says the scope aloud
}

/** The choice as POST /api/chat's `scope`; {} is every document. */
export function scopeBody(scope: ChosenScope): ScopeBody {
  switch (scope.kind) {
    case "all":
      return {};
    case "folder":
      return { folder_id: scope.folderId, include_subfolders: scope.subfolders };
    case "documents":
      return { document_ids: [...scope.ids] };
    case "other":
      return scope.body;
  }
}

/** A saved scope back into a choice. None saved, or nothing set in it, is every document. */
export function scopeFromBody(body: ScopeBody | null | undefined): ChosenScope {
  if (!body) return EVERY_DOCUMENT;
  const set = Object.entries(body)
    .filter(([key, value]) => key !== "include_subfolders" && value !== null && value !== undefined)
    .map(([key]) => key);
  if (set.length === 0) return EVERY_DOCUMENT;
  if (set.length === 1 && body.folder_id) {
    return { kind: "folder", folderId: body.folder_id, subfolders: body.include_subfolders === true };
  }
  if (set.length === 1 && body.document_ids) return { kind: "documents", ids: body.document_ids };
  return { kind: "other", body };
}

function stateOf(
  scope: ChatScope,
  changed: boolean,
  documents: string[] | null,
  announce = false,
): ScopeState {
  return { scope, changed, documents: scope.kind === "documents" ? scope.ids : documents, announce };
}

/** A new chat: every document, or the one document "Ask about this document" named. */
export function startScope(documentId: string | null): ScopeState {
  return documentId
    ? stateOf({ kind: "documents", ids: [documentId] }, true, null)
    : stateOf(EVERY_DOCUMENT, false, null);
}

/** A conversation being opened: its scope is not known yet, and nothing is sent. */
export function keptScope(): ScopeState {
  return stateOf({ kind: "kept" }, false, null);
}

/** A reopened conversation: the scope its newest question used. O(messages). */
export function restoredScope(messages: readonly Message[]): ScopeState {
  const last = messages.findLast((message) => message.role === "user");
  return stateOf(scopeFromBody(last?.scope), false, null, true);
}

/** The person picked a scope: it goes with the next question. */
export function chooseScope(state: ScopeState, scope: ChosenScope): ScopeState {
  return stateOf(scope, true, state.documents, true);
}

/** What the next question sends: the scope when it changed, else nothing (the conversation keeps its own). */
export function sentScope(state: ScopeState): ScopeBody | undefined {
  return state.changed && state.scope.kind !== "kept" ? scopeBody(state.scope) : undefined;
}

/**
 * The chip's scope as a body, for story matches (council 3 Oct 2026): what the
 * chip shows, sent every time. A conversation still loading ("kept") sends none.
 */
export function chipScope(state: ScopeState): ScopeBody {
  return state.scope.kind === "kept" ? {} : scopeBody(state.scope);
}

/** After an answer: the scope the server saved on the question, which a follow-up keeps. */
export function answeredScope(state: ScopeState, question: Message): ScopeState {
  return stateOf(scopeFromBody(question.scope), false, state.documents);
}

/**
 * The chip's words: "All documents", "Folder: Suppliers / 2026", "This
 * document: bill.pdf", "3 documents". `tree` is null while the folders load;
 * `documentName` is null until the one document's name is known.
 */
export function scopeText(
  scope: ChatScope,
  tree: FolderTree | null,
  documentName: string | null,
): string {
  switch (scope.kind) {
    case "all":
      return "All documents";
    case "folder": {
      if (tree === null) return "One folder";
      const path = pathLabel(tree, scope.folderId);
      if (!path) return "A folder that was deleted";
      return `Folder: ${path}${scope.subfolders ? ", with its subfolders" : ""}`;
    }
    case "documents":
      if (scope.ids.length === 1) return documentName ? `This document: ${documentName}` : "1 document";
      return scope.ids.length === 0 ? "No documents" : `${scope.ids.length} documents`;
    case "other":
      return "Filtered documents";
    case "kept":
      return "This conversation's documents";
  }
}

/**
 * What the status line says aloud: the chip's words once after a pick or a
 * restore, and only when the names in them are known (`namesLoading` false),
 * so a name arriving later never says it twice. Otherwise nothing.
 */
export function scopeAnnouncement(state: ScopeState, text: string, namesLoading: boolean): string {
  return state.announce && !namesLoading ? `Asking about: ${text}` : "";
}

/** Why the choices can't be changed right now, or null when they can. */
export function scopeLockedText(answerPending: boolean, conversationLoading: boolean): string | null {
  if (answerPending) return "You can change this once the answer arrives.";
  return conversationLoading ? "You can change this once the conversation loads." : null;
}

/** #720: "Find a folder by name or path": the folders whose path (its name too) holds `query`,
 * any case, in tree order, at most `most`; none for an empty query. O(f * d) for f folders of
 * depth d, stopping at the `most`th match. */
export function findFolders(
  tree: FolderTree,
  query: string,
  most = 8,
): { id: string; name: string; path: string }[] {
  const wanted = query.trim().toLocaleLowerCase();
  const found: { id: string; name: string; path: string }[] = [];
  if (!wanted) return found;
  for (const node of inTreeOrder(tree)) {
    const path = pathLabel(tree, node.folder.id);
    if (path.toLocaleLowerCase().includes(wanted)) found.push({ id: node.folder.id, name: node.folder.name, path });
    if (found.length === most) break;
  }
  return found;
}
