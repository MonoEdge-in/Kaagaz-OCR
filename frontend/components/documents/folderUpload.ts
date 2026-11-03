// A folder upload's run and its words (#484), kept out of the components so they are unit
// tested in node (tests/folder-upload.test.ts) against a fake API.
// - Start: plan the folders (the server makes them all at once), register every file as an
//   item with its folder, REGISTER_PAGE at a time, then send what is waiting.
// - Send (also Continue upload): page the server's waiting items in seq order, match each to
//   the picked files by path_key, and send the server's files_at_once (at most
//   FOLDER_UPLOADS_AT_ONCE) at a time to that item, so a retry always lands in the folder the
//   server recorded. A file missing from the pick, or whose size changed, is said and not sent.
//   Once another account signs in here, nothing more goes.
// - The state keeps counts and at most FOLDER_UPLOADS_AT_ONCE rows being sent, never a row a
//   file: the lists of what needs attention are read from the server, a page at a time.

import { failureWords } from "@/components/operator/platform";
import { ApiError } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { documentWords, failureFix } from "@/lib/status";
import type {
  FolderItemFailure,
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
import { pathKey } from "./folderPick";
import type { Manifest, Picked } from "./folderPick";
import { FALLBACK_UPLOAD_LIMITS, limitFor, sessionChangedMessage, uploadErrorText, uploadKind } from "./logic";
import { createSlots } from "./uploadQueue";

export const FOLDER_UPLOADS_AT_ONCE = 4;
export const REGISTER_PAGE = 1000;
export const ITEMS_PAGE = 500;

/** The lib/api.ts calls, given in, so tests run against a fake server. */
export interface FolderUploadApi {
  plan(body: FolderUploadPlanBody): Promise<FolderUploadPlan>;
  register(id: string, userId: string, items: NewFolderItem[]): Promise<unknown>;
  items(id: string, view: FolderItemView, after: number, limit: number): Promise<FolderItemsPage>;
  send(file: File, item: FolderUploadItem, onProgress: (fraction: number) => void): Promise<UploadResult>;
  summary(id: string): Promise<FolderUploadSummary>;
}

/** The picked folder, held in a ref: its files, paths, manifest, and path_key → file index once hashed. */
export interface FolderSource {
  picked: Picked;
  manifest: Manifest;
  keys?: Map<string, number>;
}

export type FolderTarget = { kind: "department" } | { kind: "folder"; parentId: string | null };

export type FolderPhase = "planning" | "registering" | "sending" | "done" | "stopped" | "needs_pick" | "refused";

export type FolderEvent =
  | { type: "start"; id: string; rootName: string; total: number }
  | { type: "phase"; id: string; phase: FolderPhase; message?: string }
  | { type: "planned"; id: string; rootFolderId: string }
  | { type: "counts"; id: string; summary: FolderUploadSummary }
  | { type: "sending"; id: string; itemId: string; name: string; fraction: number }
  | { type: "sent"; id: string; itemId: string; outcome: "stored" | "duplicate" | "failed" }
  | { type: "notSent"; id: string; itemId: string; name: string; message: string; retry: boolean; item: FolderUploadItem }
  | { type: "retrying"; id: string; count: number }
  | { type: "remove"; id: string };

/** The server's item failures: it recorded the item failed, so sending it again can't help. */
const ITEM_FAILURES = new Set(["too_large", "unsupported_type", "empty_file", "folder_gone", "item_refused"]);

/** Why the browser refuses a file before sending it, as the server's failure code; undefined: it may go. */
export function refusedReason(file: { name: string; size: number }, limits: UploadLimits): FolderItemFailure | undefined {
  const kind = uploadKind(file.name);
  if (kind === null) return "unsupported_type";
  if (file.size === 0) return "empty_file";
  return file.size > limitFor(kind, limits) ? "too_large" : undefined;
}

async function keyIndex(source: FolderSource): Promise<Map<string, number>> {
  if (source.keys) return source.keys;
  const keys = new Map<string, number>();
  for (let at = 0; at < source.picked.paths.length; at++) keys.set(await pathKey(source.picked.paths[at]!), at);
  source.keys = keys;
  return keys;
}

/** Plan, register, then send. `stopped` is asked before each file is sent (Stop). */
export async function startFolderUpload(
  api: FolderUploadApi,
  args: { id: string; userId: string; source: FolderSource; target: FolderTarget; merge: boolean; limits?: UploadLimits },
  emit: (event: FolderEvent) => void,
  stopped: () => boolean,
): Promise<void> {
  const { id, userId, source, target, merge } = args;
  const { picked, manifest } = source;
  emit({ type: "start", id, rootName: picked.rootName, total: manifest.fileCount });
  emit({ type: "phase", id, phase: "planning" });
  let plan: FolderUploadPlan;
  try {
    plan = await api.plan({
      id,
      user_id: userId,
      target: target.kind,
      parent_folder_id: target.kind === "folder" ? target.parentId : null,
      merge,
      folders: manifest.folders,
      file_count: manifest.fileCount,
      total_bytes: manifest.totalBytes,
      skipped_count: picked.skippedCount,
    });
  } catch (caught) {
    emit({ type: "phase", id, phase: "refused", message: uploadErrorText(caught, online()) });
    return;
  }
  emit({ type: "planned", id, rootFolderId: plan.root_folder_id });
  emit({ type: "phase", id, phase: "registering" });
  const keys = new Map<string, number>();
  try {
    for (let start = 0; start < manifest.fileCount; start += REGISTER_PAGE) {
      const page: NewFolderItem[] = [];
      for (let at = start; at < Math.min(start + REGISTER_PAGE, manifest.fileCount); at++) {
        const file = picked.files[at]!;
        const key = await pathKey(picked.paths[at]!);
        keys.set(key, at);
        const refused = refusedReason(file, args.limits ?? FALLBACK_UPLOAD_LIMITS);
        page.push({
          path_key: key,
          name: file.name.normalize("NFC"),
          folder_id: plan.folder_ids[manifest.fileFolder[at]!]!,
          size: file.size,
          ...(refused ? { refused } : {}),
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
  const halted = () => signedOut !== null || stopped();

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
      const result = await api.send(file, item, (fraction) => {
        // Whole tenths only: a browser reports about every 50 ms, and each report redraws the tray.
        if (Math.floor(fraction * 10) === Math.floor(shown * 10)) return;
        shown = fraction;
        emit({ type: "sending", id, itemId: item.id, name: item.name, fraction });
      });
      emit({ type: "sent", id, itemId: item.id, outcome: result.duplicate ? "duplicate" : "stored" });
    } catch (caught) {
      const changed = sessionChangedMessage(caught);
      signedOut ??= changed;
      if (caught instanceof ApiError && ITEM_FAILURES.has(caught.code)) {
        emit({ type: "sent", id, itemId: item.id, outcome: "failed" });
        return;
      }
      const retry = changed === null && canRetry(caught);
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
  if (signedOut !== null) emit({ type: "phase", id, phase: "stopped", message: signedOut });
  else emit({ type: "phase", id, phase: stopped() ? "stopped" : "done" });
}

/** A connection or server problem, not a refusal: the same item can be sent again. */
const canRetry = (caught: unknown) =>
  !(caught instanceof ApiError) || caught.status === 0 || caught.status === 429 || caught.status >= 500;

// --- the state the tray shows ---------------------------------------------------------

export interface SendingRow {
  itemId: string;
  name: string;
  fraction: number;
}

export interface FolderUploadView {
  id: string;
  rootName: string;
  rootFolderId: string | null;
  total: number;
  stored: number;
  duplicates: number;
  failed: number; // refused, here or by the server
  notSent: number; // not sent from this tab (its list is kept beside, in a ref)
  reading: number;
  ready: number;
  problem: number; // stored, but its reading failed or was refused
  unlisted: number; // declared at the start but never registered (listing stopped part way)
  sending: SendingRow[]; // at most FOLDER_UPLOADS_AT_ONCE
  phase: FolderPhase;
  message: string | null;
  spoken: string; // what the status bar says: changed only on a new phase or each tenth
  spokenAt: string;
}

export const EMPTY_FOLDER_UPLOADS: Readonly<Record<string, FolderUploadView>> = {};

const uploadedOf = (view: FolderUploadView) => view.stored + view.duplicates;
export const attentionOf = (view: FolderUploadView) => view.failed + view.notSent + view.problem;

/** The tray's line: "Folder “A”: 1,240 of 5,000 uploaded, 3 need attention". */
export function folderLine(view: FolderUploadView): string {
  const name = `Folder “${view.rootName}”`;
  if (view.phase === "planning") return `${name}: making its folders…`;
  if (view.phase === "registering") return `${name}: listing its files…`;
  if (view.phase === "refused") return `${name} was not uploaded.`;
  const counts = `${formatNumber(uploadedOf(view))} of ${formatNumber(view.total)} uploaded`;
  if (view.phase === "needs_pick") return `${name}: ${counts}. Choose “${view.rootName}” again to send the rest.`;
  const attention = attentionOf(view);
  const needs = attention === 0 ? "" : `, ${formatNumber(attention)} ${attention === 1 ? "needs" : "need"} attention`;
  return `${name}: ${view.phase === "stopped" ? `stopped at ${counts}` : counts}${needs}`;
}

/** The status bar's line, said politely: "Uploading folder “A”: 1,000 of 5,000 uploaded" while it sends. */
function spokenLine(view: FolderUploadView): string {
  if (view.phase !== "sending") return folderLine(view);
  return `Uploading folder “${view.rootName}”: ${formatNumber(uploadedOf(view))} of ${formatNumber(view.total)} uploaded`;
}

/** Kaagaz reading what was stored, in words; null when nothing is left to read. */
export function readingLine(view: FolderUploadView): string | null {
  if (view.reading === 0) return null;
  return `Reading: ${formatNumber(view.ready)} of ${formatNumber(view.stored)} ready. Reading can take a while for big folders.`;
}

function withSpoken(view: FolderUploadView): FolderUploadView {
  const tenth = view.total === 0 ? 0 : Math.floor((uploadedOf(view) * 10) / view.total);
  const at = `${view.phase}:${tenth}`;
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
      stored: 0,
      duplicates: 0,
      failed: 0,
      notSent: 0,
      reading: 0,
      ready: 0,
      problem: 0,
      unlisted: 0,
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
    case "phase":
      next = { ...view, phase: event.phase, message: event.message ?? null, sending: event.phase === "sending" ? view.sending : [] };
      break;
    case "planned":
      next = { ...view, rootFolderId: event.rootFolderId };
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
        unlisted: Math.max(0, summary.file_count - summary.waiting - summary.stored - summary.duplicate - summary.failed),
      };
      break;
    }
    case "sending": {
      const row = { itemId: event.itemId, name: event.name, fraction: event.fraction };
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
      next = { ...view, sending: without(event.itemId), notSent: view.notSent + 1 };
      break;
    case "retrying":
      next = { ...view, notSent: Math.max(0, view.notSent - event.count) };
      break;
  }
  return { ...state, [event.id]: withSpoken(next) };
}

/** A view for a folder upload of an earlier visit, from the server's counts. */
export function viewOfSummary(summary: FolderUploadSummary): FolderUploadView {
  const start = folderUploadsReducer(EMPTY_FOLDER_UPLOADS, {
    type: "start",
    id: summary.id,
    rootName: summary.root_name,
    total: summary.file_count,
  });
  const counted = folderUploadsReducer(start, { type: "counts", id: summary.id, summary });
  return folderUploadsReducer(counted, { type: "phase", id: summary.id, phase: summary.waiting > 0 ? "needs_pick" : "done" })[summary.id]!;
}

const MB = 1024 * 1024;

/** Why an item needs attention, with what to do, in plain words; null when it doesn't. Never retried. */
export function itemProblemText(item: FolderUploadItem, limits: UploadLimits): string | null {
  switch (item.failure_code) {
    case "too_large": {
      const kind = uploadKind(item.name) ?? "pdf";
      return `Too big: the most is ${Math.floor(limitFor(kind, limits) / MB)} MB.`;
    }
    case "unsupported_type":
      return "This kind of file can't be uploaded. Use PDF, PNG, JPG, TIFF, CSV or XLSX.";
    case "empty_file":
      return "This file is empty.";
    case "folder_gone":
      return "The folder this file was meant for was deleted after the upload started.";
    case null:
      break;
  }
  const document = item.document;
  if (document && ["failed", "rejected", "needs_reader"].includes(document.status)) {
    const code = document.status === "needs_reader" ? "needs_reader" : document.failure_code;
    const what = code ? failureWords(code) : documentWords(document.status);
    return `${what}. ${failureFix(document.status, document.failure_code)}`;
  }
  return null;
}
