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

import { SHARED } from "@/components/access/words";
import { folderPath, inTreeOrder } from "@/components/documents/folderTree";
import { isDepartment } from "@/components/files/departments";
import type { FolderTree } from "@/components/documents/folderTree";
import type { DocumentSummary, Message, ScopeBody } from "@/lib/types";

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

/** A new chat: every document, the one document "Ask about this document" named, or (#725) the
 * folder "Ask in this folder" named, with its subfolders. */
export function startScope(documentId: string | null, folderId: string | null = null): ScopeState {
  if (documentId) return stateOf({ kind: "documents", ids: [documentId] }, true, null);
  if (folderId) return stateOf({ kind: "folder", folderId, subfolders: true }, true, null);
  return stateOf(EVERY_DOCUMENT, false, null);
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
 * The chip's words, saying exactly what is chosen (#725): "All documents", "Ask in department:
 * Land & Legal", "Ask in folder: Land & Legal › Land records (and its subfolders)", "Ask about
 * file: bill.pdf", "Ask about 3 files". `tree` is null while the folders load; `documentName` is
 * null until the one document's name is known.
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
      const folder = tree.byId.get(scope.folderId)?.folder;
      if (!folder) return "A folder that was deleted";
      if (isDepartment(folder) && scope.subfolders) return `Ask in department: ${folder.name}`;
      return `Ask in folder: ${folderPathText(tree, scope.folderId)}${scope.subfolders ? " (and its subfolders)" : ""}`;
    }
    case "documents":
      if (scope.ids.length === 1) return documentName ? `Ask about file: ${documentName}` : "Ask about 1 file";
      return scope.ids.length === 0 ? "No documents" : `Ask about ${scope.ids.length} files`;
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
  return state.announce && !namesLoading ? text : ""; // #725: the words say what is chosen
}

/** Why the choices can't be changed right now, or null when they can. */
export function scopeLockedText(answerPending: boolean, conversationLoading: boolean): string | null {
  if (answerPending) return "You can change this once the answer arrives.";
  return conversationLoading ? "You can change this once the conversation loads." : null;
}

/** A folder's place, root first: "Land & Legal › Land records". */
export const folderPathText = (tree: FolderTree, folderId: string) =>
  folderPath(tree, folderId)
    .map((folder) => folder.name)
    .join(" › ");

/** One department or folder the find box matched (#720, #725). */
export interface FoundFolder {
  id: string;
  name: string;
  path: string;
  department: boolean; // a department: asked in with all its folders
}

/** #720, #725: the departments and folders whose name or place holds `query`, any case, in tree
 * order, at most `most`; none for an empty query. O(f * d) for f folders of depth d, stopping at
 * the `most`th match. */
export function findFolders(tree: FolderTree, query: string, most = 8): FoundFolder[] {
  const wanted = query.trim().toLocaleLowerCase();
  const found: FoundFolder[] = [];
  if (!wanted) return found;
  for (const node of inTreeOrder(tree)) {
    const path = folderPathText(tree, node.folder.id);
    if (path.toLocaleLowerCase().includes(wanted)) {
      found.push({ id: node.folder.id, name: node.folder.name, path, department: isDepartment(node.folder) });
    }
    if (found.length === most) break;
  }
  return found;
}

/** The most files one question asks about (backend/kaagaz/search/scope.py MAX_DOCUMENT_IDS). */
export const MAX_FILES = 50;

/** #725: the files asked about after `fileId` is pressed: out when it was in, else in, while
 * fewer than MAX_FILES are; null when it can't go in (MAX_FILES already). */
export function pickFile(picked: readonly string[], fileId: string): string[] | null {
  if (picked.includes(fileId)) return picked.filter((id) => id !== fileId);
  return picked.length < MAX_FILES ? [...picked, fileId] : null;
}

/** #725: where a file found by name is, in grey: its folder's place; "Shared with you" in a
 * folder this person can't see; "Not in a folder" at the top level. */
export function filePlace(tree: FolderTree, file: Pick<DocumentSummary, "folder_id" | "shared_with_you">): string {
  if (file.shared_with_you) return SHARED.row;
  return file.folder_id && tree.byId.has(file.folder_id) ? folderPathText(tree, file.folder_id) : "Not in a folder";
}

/** #720, #725: the words of the chip's remove button, back to all documents, naming what it takes
 * away; null when there is nothing to remove. */
export function removeScopeLabel(scope: ChatScope, tree: FolderTree | null, documentName: string | null): string | null {
  if (scope.kind === "folder") {
    const folder = tree?.byId.get(scope.folderId)?.folder;
    if (!folder) return null;
    return `Remove ${isDepartment(folder) && scope.subfolders ? "department" : "folder"} ${folder.name}`;
  }
  if (scope.kind !== "documents" || scope.ids.length === 0) return null;
  if (scope.ids.length > 1) return `Remove ${scope.ids.length} files`;
  return documentName ? `Remove file ${documentName}` : "Remove the file";
}
