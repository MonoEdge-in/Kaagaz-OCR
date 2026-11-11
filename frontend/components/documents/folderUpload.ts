// A folder upload's run and its words (#484), kept out of the components so they are unit
// tested in node (tests/folder-upload.test.ts) against a fake API.
// - Start: plan the folders (the server makes them all at once), register every file as an
//   item with its folder, REGISTER_PAGE at a time, then send what is waiting.
// - Add new files only: while listing, hash each file Kaagaz could take, one at a time (only one
//   file's bytes are held), and register it with its sha256; the server answers a file it already
//   has as "duplicate", so it is never sent. Says how many are checked every CHECKED_EVERY files.
// - Send (also Continue upload): page the server's waiting items in seq order, match each to
//   the picked files by path_key, and send the server's files_at_once (at most
//   FOLDER_UPLOADS_AT_ONCE) at a time to that item, so a retry always lands in the folder the
//   server recorded. A file missing from the pick, or whose size changed, is said and not sent.
//   Once another account signs in here, nothing more goes. Once a file is refused for storage
//   (#516), nothing more goes either: the row says the API's words once, and the files left
//   wait on the server for Continue upload, after room is made.
// - The state keeps counts and at most FOLDER_UPLOADS_AT_ONCE rows being sent, never a row a
//   file: the lists of what needs attention are read from the server, a page at a time.

import { failureWords } from "@/components/operator/platform";
import { ApiError, isStorageFull, rateLimitWait } from "@/lib/api";
import type { UploadWaiting } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { documentWords, failureFix } from "@/lib/status";
import type {
  FolderItemView,
  FolderItemsPage,
  FolderUploadItem,
  FolderUploadPlan,
  FolderUploadPlanBody,
  FolderUploadSummary,
  NewFolderItem,
  UploadLimits,
  UploadResult,
} from "@/lib/types";
import { fileSha256, pathKey, refusedReason } from "./folderPick";
import type { Manifest, Picked } from "./folderPick";
import type { NotSentRow } from "./FolderUploadRow";
import {
  UNSUPPORTED_UPLOAD,
  canSendAgain,
  limitFor,
  sessionChangedMessage,
  uploadErrorText,
  uploadKind,
} from "./logic";
import { createSlots } from "./uploadQueue";

const FOLDER_UPLOADS_AT_ONCE = 4;
const REGISTER_PAGE = 1000;
/** How often "checking your files: N of M" moves on. */
const CHECKED_EVERY = 50;
export const ITEMS_PAGE = 500;
/** The files not sent shown on a row; the rest are counted. */
const NOT_SENT_SHOWN = 20;

/** The lib/api.ts calls, given in, so tests run against a fake server. */
export interface FolderUploadApi {
  plan(body: FolderUploadPlanBody): Promise<FolderUploadPlan>;
  register(id: string, userId: string, items: NewFolderItem[]): Promise<unknown>;
  items(id: string, view: FolderItemView, after: number, limit: number): Promise<FolderItemsPage>;
  send(file: File, item: FolderUploadItem, onProgress: (fraction: number) => void, onWaiting?: UploadWaiting): Promise<UploadResult>;
  summary(id: string): Promise<FolderUploadSummary>;
}

/** The picked folder, held in a ref: its files, paths, manifest, and path_key → file index once hashed. */
export interface FolderSource {
  picked: Picked;
  manifest: Manifest;
  keys?: Map<string, number>;
}

export type FolderTarget = { kind: "department" } | { kind: "folder"; parentId: string | null };

/** Why a file being sent waits to go again (lib/api.ts retryWhileOffline). */
type Waiting = "connection" | "server";

// stopping: Stop was pressed; the files being sent finish, then it is stopped.
type FolderPhase = "planning" | "registering" | "sending" | "stopping" | "done" | "stopped" | "needs_pick" | "refused";
const RUNNING: readonly FolderPhase[] = ["planning", "registering", "sending"];

export type FolderEvent =
  | { type: "start"; id: string; rootName: string; total: number }
  | { type: "phase"; id: string; phase: FolderPhase; message?: string }
  | { type: "planned"; id: string; rootFolderId: string }
  | { type: "checked"; id: string; done: number }
  | { type: "counts"; id: string; summary: FolderUploadSummary }
  | { type: "sending"; id: string; itemId: string; name: string; fraction: number; waiting?: Waiting }
  | { type: "sent"; id: string; itemId: string; outcome: "stored" | "duplicate" | "failed" }
  | { type: "notSent"; id: string; itemId: string; name: string; message: string; retry: boolean; item: FolderUploadItem }
  | { type: "remove"; id: string };

/** The server's item failures: it recorded the item failed, so sending it again can't help. */
const ITEM_FAILURES = new Set(["too_large", "unsupported_type", "empty_file", "folder_gone", "item_refused"]);

export { refusedReason };

/** The plan, asked again after the server's Retry-After while another plan of the organisation is built. */
async function planWhenFree(api: FolderUploadApi, body: FolderUploadPlanBody): Promise<FolderUploadPlan> {
  for (;;) {
    try {
      return await api.plan(body);
    } catch (caught) {
      if (!(caught instanceof ApiError && caught.code === "plan_busy")) throw caught;
      await new Promise((resolve) => setTimeout(resolve, rateLimitWait(caught, 2000) ?? 2000));
    }
  }
}

/** The SHA-256 of a file Kaagaz could take, for "Add new files only". Undefined for a refused kind, an
 * empty file, one over its limit, or one the browser can't read: those are registered and sent as today. */
async function contentHash(file: File, limits: UploadLimits | undefined): Promise<string | undefined> {
  const kind = uploadKind(file.name);
  if (kind === null || file.size === 0 || file.size > limitFor(kind, limits)) return undefined;
  try {
    return await fileSha256(file);
  } catch {
    return undefined;
  }
}

async function keyIndex(source: FolderSource): Promise<Map<string, number>> {
  if (source.keys) return source.keys;
  const keys = new Map<string, number>();
  for (let at = 0; at < source.picked.paths.length; at++) keys.set(await pathKey(source.picked.paths[at]!), at);
  source.keys = keys;
  return keys;
}

/** Plan, register, then send. `stopped` (Stop) is asked after the plan, before each page of the
 * listing, and before each file is checked or sent; Continue upload lists again with the same id. */
export async function startFolderUpload(
  api: FolderUploadApi,
  args: {
    id: string;
    userId: string;
    source: FolderSource;
    target: FolderTarget;
    merge: boolean;
    onlyNew?: boolean;
    limits?: UploadLimits;
  },
  emit: (event: FolderEvent) => void,
  stopped: () => boolean,
): Promise<void> {
  const { id, userId, source, target, merge, onlyNew = false } = args;
  const { picked, manifest } = source;
  emit({ type: "start", id, rootName: picked.rootName, total: manifest.fileCount });
  emit({ type: "phase", id, phase: "planning" });
  let plan: FolderUploadPlan;
  try {
    plan = await planWhenFree(api, {
      id,
      user_id: userId,
      target: target.kind,
      parent_folder_id: target.kind === "folder" ? target.parentId : null,
      merge,
      folders: manifest.folders,
      file_count: manifest.fileCount,
      total_bytes: manifest.totalBytes,
      skipped_count: picked.skippedCount,
      only_new: onlyNew,
    });
  } catch (caught) {
    emit({ type: "phase", id, phase: "refused", message: uploadErrorText(caught, online()) });
    return;
  }
  emit({ type: "planned", id, rootFolderId: plan.root_folder_id });
  if (stopped()) return emit({ type: "phase", id, phase: "stopped" });
  emit({ type: "phase", id, phase: "registering" });
  if (onlyNew) emit({ type: "checked", id, done: 0 });
  const keys = new Map<string, number>();
  try {
    for (let start = 0; start < manifest.fileCount; start += REGISTER_PAGE) {
      if (stopped()) return emit({ type: "phase", id, phase: "stopped" });
      const page: NewFolderItem[] = [];
      for (let at = start; at < Math.min(start + REGISTER_PAGE, manifest.fileCount); at++) {
        // Stopped while checking: the pages registered stay; Continue plans the same id again.
        if (onlyNew && stopped()) {
          emit({ type: "phase", id, phase: "stopped" });
          return;
        }
        const file = picked.files[at]!;
        const key = await pathKey(picked.paths[at]!);
        keys.set(key, at);
        const refused = refusedReason(file);
        // One file at a time, awaited: never all at once.
        const sha256 = onlyNew ? await contentHash(file, args.limits) : undefined;
        const done = at + 1;
        if (onlyNew && (done % CHECKED_EVERY === 0 || done === manifest.fileCount)) emit({ type: "checked", id, done });
        page.push({
          path_key: key,
          name: file.name.normalize("NFC"),
          folder_id: plan.folder_ids[manifest.fileFolder[at]!]!,
          size: file.size,
          ...(refused ? { refused } : {}),
          ...(sha256 ? { sha256 } : {}),
        });
      }
      await api.register(id, userId, page);
    }
  } catch (caught) {
    const why = sessionChangedMessage(caught) ?? `Not every file could be listed. ${uploadErrorText(caught, online())}`;
    emit({ type: "phase", id, phase: "stopped", message: why });
    return;
  }
  source.keys = keys;
  // How many were already in Kaagaz, said while the rest are sent; sending counts again at the end.
  if (onlyNew) await api.summary(id).then((summary) => emit({ type: "counts", id, summary }), () => undefined);
  await sendWaiting(api, { id, userId, source, atOnce: args.limits?.folder_upload?.files_at_once }, emit, stopped);
}

const online = () => typeof navigator === "undefined" || navigator.onLine !== false;

/** Send every item still waiting (or only these, for Retry), each to its own item, matched to the
 * pick by path_key; `atOnce` (the server's files_at_once) at a time, at most FOLDER_UPLOADS_AT_ONCE. */
export async function sendWaiting(
  api: FolderUploadApi,
  args: { id: string; userId: string; source: FolderSource; only?: readonly FolderUploadItem[]; atOnce?: number },
  emit: (event: FolderEvent) => void,
  stopped: () => boolean,
): Promise<void> {
  const { id, source } = args;
  const root = source.picked.rootName;
  emit({ type: "phase", id, phase: "sending" });
  const keys = await keyIndex(source);
  const slots = createSlots(Math.max(1, Math.min(args.atOnce ?? FOLDER_UPLOADS_AT_ONCE, FOLDER_UPLOADS_AT_ONCE)));
  let signedOut: string | null = null;
  let full: string | null = null; // the API's storage_full words (#516)
  const halted = () => signedOut !== null || full !== null || stopped();

  async function sendOne(item: FolderUploadItem) {
    if (halted()) return;
    const at = keys.get(item.path_key);
    const file = at === undefined ? undefined : source.picked.files[at];
    const notSent = (message: string, retry: boolean) =>
      emit({ type: "notSent", id, itemId: item.id, name: item.name, message, retry, item });
    if (!file) return notSent(`This file isn’t in the folder you chose. Choose “${root}” again with all its files.`, false);
    if (file.size !== item.size) {
      return notSent(`This file changed after you chose the folder. Choose “${root}” again to send it as it is now.`, false);
    }
    emit({ type: "sending", id, itemId: item.id, name: item.name, fraction: 0 });
    let shown = 0;
    try {
      const result = await api.send(
        file,
        item,
        (fraction) => {
          // Whole tenths only: a browser reports about every 50 ms, and each report redraws the tray.
          if (Math.floor(fraction * 10) === Math.floor(shown * 10)) return;
          shown = fraction;
          emit({ type: "sending", id, itemId: item.id, name: item.name, fraction });
        },
        // The row says while the file waits for the connection or the server (production QA, 5 Nov 2026).
        (waiting, why) =>
          emit({ type: "sending", id, itemId: item.id, name: item.name, fraction: shown, ...(waiting ? { waiting: why ?? "connection" } : {}) }),
      );
      emit({ type: "sent", id, itemId: item.id, outcome: result.duplicate ? "duplicate" : "stored" });
    } catch (caught) {
      if (isStorageFull(caught)) {
        full ??= uploadErrorText(caught, online()); // the item stays waiting on the server
        return;
      }
      const changed = sessionChangedMessage(caught);
      signedOut ??= changed;
      if (caught instanceof ApiError && ITEM_FAILURES.has(caught.code)) {
        emit({ type: "sent", id, itemId: item.id, outcome: "failed" });
        return;
      }
      const retry = changed === null && canSendAgain(caught);
      notSent(changed ?? uploadErrorText(caught, online()), retry);
    }
  }

  try {
    if (args.only) await Promise.all(args.only.map((item) => slots(() => sendOne(item))));
    for (let after = -1; !args.only && !halted(); ) {
      const page = await api.items(id, "waiting", after, ITEMS_PAGE);
      await Promise.all(page.items.map((item) => slots(() => sendOne(item))));
      if (page.next_after === null) break;
      after = page.next_after;
    }
    emit({ type: "counts", id, summary: await api.summary(id) });
  } catch (caught) {
    signedOut ??= sessionChangedMessage(caught) ?? `Kaagaz could not list the files still to send. ${uploadErrorText(caught, online())}`;
  }
  const why = signedOut ?? full;
  if (why !== null) emit({ type: "phase", id, phase: "stopped", message: why });
  else emit({ type: "phase", id, phase: stopped() ? "stopped" : "done" });
}

// --- the state the tray shows ---------------------------------------------------------

interface SendingRow {
  itemId: string;
  name: string;
  fraction: number;
  waiting?: Waiting;
}

export interface FolderUploadView {
  id: string;
  rootName: string; // the picked folder's own name, "A", never Kaagaz's "A (2)"
  rootFolderId: string | null;
  total: number;
  checked: number | null; // files checked for "Add new files only" while listing; null when not checking
  stored: number;
  duplicates: number;
  failed: number; // refused, here or by the server
  reading: number;
  ready: number;
  problem: number; // stored, but its reading failed or was refused
  held: number; // stored, waiting for the reading pause (#478): "waiting", never "not sent yet"
  heldMessage: string | null; // the pause's own words (the server's), while any is held
  sending: SendingRow[]; // at most FOLDER_UPLOADS_AT_ONCE
  phase: FolderPhase;
  message: string | null;
  spoken: string; // what the status bar says: changed only on a new phase or each tenth
  spokenAt: string;
}

export const EMPTY_FOLDER_UPLOADS: Readonly<Record<string, FolderUploadView>> = {};

const uploadedOf = (view: FolderUploadView) => view.stored + view.duplicates;
/** Files not sent yet (#478: "Waiting" is the reading pause's word): listed or not, none sent. */
const notSentYetOf = (view: FolderUploadView) => Math.max(0, view.total - uploadedOf(view) - view.failed);
/** What needs attention, as the server's "attention" list holds it: the files not sent are listed apart. */
export const attentionOf = (view: FolderUploadView) => view.failed + view.problem;

/** The tray's line: "Folder “A”: 1,240 of 5,000 uploaded, 3 need attention". */
export function folderLine(view: FolderUploadView): string {
  const name = `Folder “${view.rootName}”`;
  if (view.phase === "planning") return `${name}: making its folders…`;
  if (view.phase === "registering") {
    if (view.checked === null) return `${name}: listing its files…`;
    return `${name}: checking your files: ${formatNumber(view.checked)} of ${formatNumber(view.total)}`;
  }
  if (view.phase === "refused") return `${name} was not uploaded.`;
  const counts = `${formatNumber(uploadedOf(view))} of ${formatNumber(view.total)} uploaded`;
  const left = notSentYetOf(view);
  const notYet = left === 0 ? "" : `, ${formatNumber(left)} not sent yet`;
  const already = view.duplicates === 0 ? "" : `, ${formatNumber(view.duplicates)} already in Kaagaz`;
  if (view.phase === "needs_pick") return `${name}: ${counts}${already}${notYet}. Choose “${view.rootName}” again to send them.`;
  const attention = attentionOf(view);
  const needs = attention === 0 ? "" : `, ${formatNumber(attention)} ${attention === 1 ? "needs" : "need"} attention`;
  return `${name}: ${view.phase === "stopped" ? `stopped at ${counts}${already}${notYet}` : `${counts}${already}`}${needs}`;
}

/** The status bar's line, said politely: "Uploading folder “A”: 1,000 of 5,000 uploaded" while it sends. */
function spokenLine(view: FolderUploadView): string {
  if (view.phase !== "sending") return folderLine(view);
  return `Uploading folder “${view.rootName}”: ${formatNumber(uploadedOf(view))} of ${formatNumber(view.total)} uploaded`;
}

/** Kaagaz reading what was stored, in words, with the files the reading pause holds as
 * "waiting" (#478); null when nothing is left to read. */
export function readingLine(view: FolderUploadView): string | null {
  if (view.reading === 0 && view.held === 0) return null;
  const waiting = view.held === 0 ? "" : `, ${formatNumber(view.held)} waiting`;
  return `Reading: ${formatNumber(view.ready)} of ${formatNumber(view.stored)} ready${waiting}. Reading can take a while for big folders.`;
}

function withSpoken(view: FolderUploadView): FolderUploadView {
  const tenth = (count: number) => (view.total === 0 ? 0 : Math.floor((count * 10) / view.total));
  const at = `${view.phase}:${tenth(uploadedOf(view))}:${view.checked === null ? "" : tenth(view.checked)}`;
  return at === view.spokenAt ? view : { ...view, spoken: spokenLine(view), spokenAt: at };
}

/** O(1) an event: counts change, and the sending rows stay at most FOLDER_UPLOADS_AT_ONCE. */
export function folderUploadsReducer(
  state: Readonly<Record<string, FolderUploadView>>,
  event: FolderEvent,
): Readonly<Record<string, FolderUploadView>> {
  if (event.type === "start") {
    const fresh: FolderUploadView = {
      id: event.id,
      rootName: event.rootName,
      rootFolderId: null,
      total: event.total,
      checked: null,
      stored: 0,
      duplicates: 0,
      failed: 0,
      reading: 0,
      ready: 0,
      problem: 0,
      held: 0,
      heldMessage: null,
      sending: [],
      phase: "planning",
      message: null,
      spoken: "",
      spokenAt: "",
    };
    return { ...state, [event.id]: withSpoken(fresh) };
  }
  if (event.type === "remove") {
    const next = { ...state };
    delete next[event.id];
    return next;
  }
  const view = state[event.id];
  if (!view) return state;
  const without = (itemId: string) => view.sending.filter((row) => row.itemId !== itemId);
  let next: FolderUploadView;
  switch (event.type) {
    case "phase": {
      // Stop pressed just as the run ended changes nothing: the row is not left stopping. While
      // it stops, the run going on to its next step ("sending") doesn't bring Stop back.
      if (event.phase === "stopping" && !RUNNING.includes(view.phase)) return state;
      const phase = view.phase === "stopping" && RUNNING.includes(event.phase) ? "stopping" : event.phase;
      const keep = phase === "sending" || phase === "stopping";
      next = { ...view, phase, message: event.message ?? null, sending: keep ? view.sending : [] };
      break;
    }
    case "planned":
      next = { ...view, rootFolderId: event.rootFolderId };
      break;
    case "checked":
      next = { ...view, checked: event.done };
      break;
    case "counts": {
      const { summary } = event;
      // The name stays the picked folder's ("A", to choose again), not Kaagaz's "A (2)". The
      // list (after a reload) leaves the reading counts out (null): the last ones are kept.
      next = {
        ...view,
        rootFolderId: summary.root_folder_id,
        total: summary.file_count,
        stored: summary.stored,
        duplicates: summary.duplicate,
        failed: summary.failed,
        reading: summary.reading ?? view.reading,
        ready: summary.ready ?? view.ready,
        problem: summary.problem ?? view.problem,
        held: summary.held ?? view.held,
        heldMessage: summary.held === null ? view.heldMessage : summary.held_message,
      };
      break;
    }
    case "sending": {
      const row = { itemId: event.itemId, name: event.name, fraction: event.fraction, ...(event.waiting ? { waiting: event.waiting } : {}) };
      const rest = without(event.itemId);
      next = { ...view, sending: [...rest, row].slice(-FOLDER_UPLOADS_AT_ONCE) };
      break;
    }
    case "sent":
      next = {
        ...view,
        sending: without(event.itemId),
        stored: view.stored + (event.outcome === "stored" ? 1 : 0),
        duplicates: view.duplicates + (event.outcome === "duplicate" ? 1 : 0),
        failed: view.failed + (event.outcome === "failed" ? 1 : 0),
      };
      break;
    case "notSent":
      next = { ...view, sending: without(event.itemId) };
      break;
  }
  return { ...state, [event.id]: withSpoken(next) };
}

/**
 * A folder upload of an earlier visit to bring back as "Choose “A” again": files still waiting,
 * or a listing that stopped part way (listed: false, so the folder picked again is planned and
 * listed again with the same id and place). Null when nothing is left to send.
 */
export function restoredUpload(
  summary: FolderUploadSummary,
): { events: FolderEvent[]; listed: boolean; again: { target: FolderTarget; merge: boolean; onlyNew: boolean } } | null {
  const listed = summary.registered >= summary.file_count;
  if (summary.waiting === 0 && listed) return null;
  const { id } = summary;
  return {
    events: [
      { type: "start", id, rootName: summary.source_name, total: summary.file_count },
      { type: "counts", id, summary },
      { type: "phase", id, phase: "needs_pick" },
    ],
    listed,
    again: {
      target: summary.target === "department" ? { kind: "department" } : { kind: "folder", parentId: summary.parent_folder_id },
      merge: summary.merge,
      onlyNew: summary.merge, // added to a folder already there: listed again, it checks again
    },
  };
}

/** Said when the folder picked to continue is another one; null when it is the same. */
export const wrongFolderText = (rootName: string, pickedName: string) =>
  pickedName === rootName ? null : `This is “${pickedName}”. Choose “${rootName}” to continue.`;

/** The files not sent a row shows: the first NOT_SENT_SHOWN, kept as they come, and how many in all. */
export function withNotSent(
  shown: { rows: NotSentRow[]; total: number },
  row: NotSentRow,
  total: number,
): { rows: NotSentRow[]; total: number } {
  return { rows: shown.rows.length < NOT_SENT_SHOWN ? [...shown.rows, row] : shown.rows, total };
}

const MB = 1024 * 1024;
// The server's own words for it (backend views.zip_message).
const KEPT_ZIP = "Kept as a file, not unpacked: Kaagaz unpacks a zip only when it is uploaded on its own.";

/** Why an item needs attention, with what to do, in plain words; null when it doesn't. Never retried. */
export function itemProblemText(item: FolderUploadItem, limits: UploadLimits): string | null {
  switch (item.failure_code) {
    case "too_large": {
      const kind = uploadKind(item.name) ?? "pdf";
      return `Too big: the most is ${Math.floor(limitFor(kind, limits) / MB)} MB.`;
    }
    case "unsupported_type":
      return UNSUPPORTED_UPLOAD;
    case "empty_file":
      return "This file is empty.";
    case "folder_gone":
      return "The folder this file was meant for was deleted after the upload started.";
    case "bad_name":
      return "Kaagaz can’t keep this file’s name: it has a hidden character, or another file in its folder has the same name. Rename it, then upload it again.";
    case null:
      break;
  }
  const document = item.document;
  // #549: a zip in a folder upload is kept as a file by design; say so, never "Failed".
  if (document?.status === "needs_reader" && uploadKind(item.name) === "zip") return KEPT_ZIP;
  if (document && ["failed", "rejected", "needs_reader"].includes(document.status)) {
    const code = document.status === "needs_reader" ? "needs_reader" : document.failure_code;
    const what = code ? failureWords(code) : documentWords(document.status);
    return `${what}. ${failureFix(document.status, document.failure_code)}`;
  }
  return null;
}
