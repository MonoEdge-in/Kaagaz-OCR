// The batch tray (PRD 5 U1), kept pure so it is unit tested in node
// (tests/batch-tray.test.ts): one line and one bar for each drop of files, then its
// files in the order they were dropped (changed on purpose, scan states, 16 Oct 2026:
// worst first moved a file under the pointer each time one changed state), and the
// status bar's line for all of them (statusBarText). A drop is remembered in this browser for 24 hours so the tray
// comes back after a reload (from GET /api/batches/{id}): only its id and two counts
// the server does not keep (files refused as already uploaded, and files not sent),
// never a file name. Signing out forgets them (AppShell). Every storage access is
// wrapped: blocked site data only means the tray does not come back.

import { ApiError } from "@/lib/api";
import { documentFraction, longerThanUsual } from "@/lib/progress";
import type { BatchView, DocumentSummary } from "@/lib/types";
import { attentionWords } from "./library";
import {
  counted,
  estimateIngestSeconds,
  estimatePages,
  flaggedCount,
  flaggedWords,
  pagesToCheck,
  uploadKind,
} from "./logic";
import type { UploadEntry } from "./uploadQueue";
import { NO_TIMES, jobFraction } from "./uploadStages";

export const MAX_FILES_PER_BATCH = 100; // the server's MAX_FILES_PER_BATCH (409 batch_full past it)
export const TOO_MANY_FILES = "Up to 100 files at a time. Drop the rest after these start.";
export const KEEP_MS = 24 * 60 * 60 * 1000;
export const STORE_KEY = "kaagaz.uploads.batches";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** A new batch id: a random UUID (v4), made from getRandomValues, which works on plain http too. */
export function newBatchId(random: (bytes: Uint8Array) => Uint8Array = (b) => crypto.getRandomValues(b)): string {
  const bytes = random(new Uint8Array(16));
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

// --- rows -------------------------------------------------------------------------

export type RowKind = "failed" | "stopped" | "attention" | "waiting" | "sending" | "reading" | "already" | "ready";

// Every kind, for the total (the tray lists files in the order they were dropped).
const ROW_KINDS: readonly RowKind[] = [
  "failed",
  "stopped",
  "attention",
  "waiting",
  "sending",
  "reading",
  "already",
  "ready",
];

/** What a file's row is now, for the counts and the order. */
export function rowKind(entry: Pick<UploadEntry, "state">): RowKind {
  const { state } = entry;
  switch (state.kind) {
    case "error":
      return "failed";
    case "duplicate":
    case "restoring":
    case "restored":
      return "already";
    case "waiting":
    case "sending":
    case "saving":
      return "sending";
    case "reading": {
      // As the batch API counts it: no progress yet, or held back (Q6), is waiting.
      const step = state.document.progress?.step;
      return step === undefined || step === "waiting" ? "waiting" : "reading";
    }
    case "done":
      if (!state.document) return "ready";
      if (state.document.status !== "ready") {
        // PRD 5 section 12: a file of an organisation disabled mid-batch is stopped, not failed.
        return state.document.failure_code === "customer_disabled" ? "stopped" : "failed";
      }
      return attentionWords(state.document).length > 0 ? "attention" : "ready";
  }
}

// --- one drop -----------------------------------------------------------------------

export interface TrayCounts {
  total: number;
  ready: number;
  reading: number;
  waiting: number;
  sending: number;
  attention: number;
  failed: number;
  stopped: number;
  already: number;
}

/** The counts of one drop's rows; ``stored`` adds what a reload lost (already uploaded, not sent). */
export function trayCounts(
  entries: readonly Pick<UploadEntry, "state">[],
  stored: { already: number; notSent: number } = { already: 0, notSent: 0 },
): TrayCounts {
  const counts: TrayCounts = {
    total: 0,
    ready: 0,
    reading: 0,
    waiting: 0,
    sending: 0,
    attention: 0,
    failed: 0,
    stopped: 0,
    already: 0,
  };
  for (const entry of entries) counts[rowKind(entry)] += 1;
  counts.already = Math.max(counts.already, stored.already);
  counts.failed = Math.max(counts.failed, stored.notSent);
  counts.total = ROW_KINDS.reduce((sum, kind) => sum + counts[kind], 0);
  return counts;
}

const files = (n: number) => `${n} ${n === 1 ? "file" : "files"}`;

/** "Batch of 50 files · 31 ready · 12 reading · 4 waiting · 2 need attention · 1 already uploaded". */
export function summaryText(counts: TrayCounts): string {
  const parts = [
    `Batch of ${files(counts.total)}`,
    counts.ready > 0 ? `${counts.ready} ready` : "",
    counts.sending > 0 ? `${counts.sending} sending` : "",
    counts.reading > 0 ? `${counts.reading} reading` : "",
    counts.waiting > 0 ? `${counts.waiting} waiting` : "",
    counts.attention > 0 ? `${counts.attention} ${counts.attention === 1 ? "needs" : "need"} attention` : "",
    counts.failed > 0 ? `${counts.failed} failed` : "",
    counts.stopped > 0 ? `${counts.stopped} stopped` : "",
    counts.already > 0 ? `${counts.already} already uploaded` : "",
  ];
  return parts.filter(Boolean).join(" · ");
}

/**
 * U1's words for a file with an unreadable page: "Page n to check", then what
 * happened and the fix (null when the server could say neither); null for a file with
 * no unreadable page to look at, or from a listing without the field. ``canReread``:
 * a reviewer or an admin (canReprocess); a member can't read it again, so a "Read ...
 * again" fix becomes "Upload a better scan." (council 16 Oct 2026).
 */
export function unreadableWords(
  document: Pick<DocumentSummary, "first_unreadable">,
  canReread: boolean,
): { title: string; detail: string | null } | null {
  const page = document.first_unreadable;
  if (!page) return null;
  const fix = !canReread && page.fix?.startsWith("Read ") ? "Upload a better scan." : page.fix;
  const detail = [page.says, fix].filter(Boolean).join(" ");
  return { title: `Page ${page.page_no} to check`, detail: detail || null };
}

/**
 * "All values verified" (U1) for a ready file with values and nothing on the attention
 * list; null otherwise, so a file with no values (a letter, a photo of a page), or from a
 * listing without the count, is just Ready.
 */
export function verifiedWords(
  document: Pick<
    DocumentSummary,
    "status" | "values_found" | "pages_unreadable" | "pages_uncertain" | "values_to_review" | "words_unsure"
  >,
): string | null {
  if (document.status !== "ready" || !((document.values_found ?? 0) > 0)) return null;
  return attentionWords(document).length === 0 ? "All values verified" : null;
}

/** "About 6 min left": whole minutes, at least 1 (an estimate always says "about"). */
export function aboutLeft(seconds: number): string {
  return `About ${Math.max(1, Math.ceil((Number.isFinite(seconds) ? seconds : 0) / 60))} min left`;
}

/**
 * How long the drop has left: the batch's own time_left (the documents' longest while it
 * has none), and whether any of its files is taking longer than usual by its row's rule
 * (longerThanUsual); null when nothing is left to read and none is taking longer.
 */
function dropLeft(
  view: Pick<BatchView, "time_left" | "documents">,
  now: number,
): { seconds: number; longer: boolean } | null {
  const longer = view.documents.some(
    (document) => document.progress && document.time_left && longerThanUsual(document.time_left, document.progress.step, now),
  );
  const own = view.time_left?.seconds;
  if (!longer && (own === undefined || !Number.isFinite(own) || own <= 0)) return null;
  const longest = Math.max(0, ...view.documents.map((document) => document.time_left?.seconds ?? 0));
  return { seconds: own ?? longest, longer };
}

/**
 * "About 6 min left" for the whole drop; "Taking longer than usual · About 6 min left" while
 * any of its files is; null when nothing is left to read or it is not known.
 */
export function leftText(
  view: Pick<BatchView, "time_left" | "documents"> | null,
  now = performance.now(),
): string | null {
  const left = view ? dropLeft(view, now) : null;
  if (left === null) return null;
  return left.longer ? `Taking longer than usual · ${aboutLeft(left.seconds)}` : aboutLeft(left.seconds);
}

const PAGES_DONE_AFTER_READING = new Set(["rereading", "understanding", "indexing"]);

/**
 * The status bar's one line for this tab's uploads (scan states, 16 Oct 2026; brief C: progress
 * in the status bar, never a modal): "Uploading invoice_march.pdf · 14 pages", "Scanning 2
 * documents · page 3 of 14 · About 1 min left", "2 documents waiting to be read", and once nothing is left "Scan complete · 3
 * documents · 12 flagged for review" (flagged: flaggedCount, logic.ts). "" with no uploads.
 * "page 3 of 14" counts the pages of the documents being read together.
 */
export function statusBarText(
  entries: readonly Pick<UploadEntry, "state" | "name" | "pages" | "batchId">[],
  views: Readonly<Record<string, Pick<BatchView, "time_left" | "documents">>>,
  now: number,
): string {
  const sending = entries.filter((entry) => ["waiting", "sending", "saving"].includes(entry.state.kind));
  const parts: string[] = [];
  const [one] = sending;
  if (sending.length === 1 && one) parts.push(`Uploading ${one.name}${one.pages ? ` · ${counted(one.pages, "page")}` : ""}`);
  else if (sending.length > 1) parts.push(`Uploading ${sending.length} files`);
  // U7 (QA 17 Oct): a document with no progress is waiting for the reader, counted on its own.
  const followed = entries.flatMap((entry) => (entry.state.kind === "reading" ? [entry.state.document] : []));
  const held = (document: { progress?: { step: string } | null }) => !document.progress || document.progress.step === "waiting";
  const waiting = followed.filter(held).length;
  const reading = entries.filter((entry) => entry.state.kind === "reading" && !held(entry.state.document));
  if (reading.length > 0) {
    parts.push(`Scanning ${counted(reading.length, "document")}`);
    let done = 0;
    let total = 0;
    let onPage = false;
    for (const entry of reading) {
      const progress = entry.state.kind === "reading" ? entry.state.document.progress : null;
      if (!progress || progress.total <= 0) continue;
      total += progress.total;
      if (progress.step === "reading") {
        onPage = true;
        done += Math.min(progress.done, progress.total);
      } else if (PAGES_DONE_AFTER_READING.has(progress.step)) done += progress.total;
    }
    if (onPage) parts.push(`page ${Math.min(done + 1, total)} of ${total}`);
    const lefts = [...new Set(reading.map((entry) => entry.batchId))].flatMap((id) => {
      const view = id === undefined ? undefined : views[id];
      const left = view ? dropLeft(view, now) : null;
      return left ? [left] : [];
    });
    if (lefts.length > 0) {
      const about = aboutLeft(Math.max(...lefts.map((left) => left.seconds)));
      parts.push(lefts.some((left) => left.longer) ? `Taking longer than usual · ${about}` : about);
    }
  }
  if (waiting > 0) parts.push(`${counted(waiting, "document")} waiting to be read`);
  return parts.length > 0 ? parts.join(" · ") : finishedText(entries);
}

/** "Scan complete · 3 documents · Nothing flagged · 1 not uploaded"; "" with no uploads. */
function finishedText(entries: readonly Pick<UploadEntry, "state">[]): string {
  if (entries.length === 0) return "";
  const ready = entries.flatMap((entry) =>
    entry.state.kind === "done" && entry.state.document?.status === "ready" ? [entry.state.document] : [],
  );
  const counts = trayCounts(entries);
  const notSent = entries.filter((entry) => entry.state.kind === "error").length;
  const notRead = counts.failed - notSent + counts.stopped;
  const uploaded = entries.filter((entry) => entry.state.kind === "done" && !entry.state.document).length;
  const sum = (count: (document: DocumentSummary) => number) =>
    ready.reduce((total, document) => total + count(document), 0);
  const parts = [
    ready.length > 0
      ? `Scan complete · ${counted(ready.length, "document")} · ${flaggedWords(sum(flaggedCount), sum(pagesToCheck))}`
      : "Uploads finished",
    uploaded > 0 ? `${uploaded} uploaded` : "",
    counts.already > 0 ? `${counts.already} already uploaded` : "",
    notRead > 0 ? `${notRead} failed` : "",
    notSent > 0 ? `${notSent} not uploaded` : "",
  ];
  return parts.filter(Boolean).join(" · ");
}

/**
 * The drop's bar, 0 to 1 (progress fix, 16 Oct 2026): each stored file's own fraction (its
 * row's, documentFraction; a finished file whole), weighed by its pages (1 while not
 * known). A file not stored yet counts by its expected pages, and one still sending or
 * saving by its row's bar (jobFraction at ``now``), so a one-file drop's bar is its row's.
 */
export function batchFraction(
  view: Pick<BatchView, "documents"> | null,
  entries: readonly Pick<UploadEntry, "state" | "name" | "size" | "pages" | "times">[],
  now = 0,
): number {
  const stored = new Set(view?.documents.map((document) => document.id) ?? []);
  let total = 0;
  let read = 0;
  for (const document of view?.documents ?? []) {
    const pages = document.page_count ?? 1;
    const busy = document.status === "received" || document.status === "clean";
    total += pages;
    read += pages * (busy ? (documentFraction(document.progress?.step, document.time_left, now) ?? 0) : 1);
  }
  for (const entry of entries) {
    const { state } = entry;
    const kind = uploadKind(entry.name);
    if (kind === null) continue;
    const moving = state.kind === "sending" || state.kind === "saving";
    const unknown = state.kind === "reading" && !stored.has(state.documentId);
    if (!moving && !unknown && state.kind !== "waiting") continue;
    const pages = estimatePages(kind, entry.size, entry.pages ?? null);
    total += pages;
    if (moving || unknown) {
      const readSeconds = estimateIngestSeconds(kind, entry.size, 1, entry.pages ?? null);
      read += pages * jobFraction(state, entry.size, readSeconds, entry.times ?? NO_TIMES, now);
    }
  }
  return total > 0 ? Math.min(1, Math.max(0, read / total)) : 0;
}

// --- remembered in this browser -------------------------------------------------------

export interface StoredBatch {
  id: string;
  at: number; // Date.now() when it was dropped
  already: number; // refused as already uploaded: the batch API does not list them
  notSent: number; // not sent: after a reload the file must be chosen again
}

type Store = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** localStorage, or null where reading it throws (blocked site data, some private windows). */
export function batchStore(): Store | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

const whole = (value: unknown) => (typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null);

/** The drops of the last 24 hours, well-formed only, newest first. */
export function readStored(store: Store | null, now: number): StoredBatch[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(store?.getItem(STORE_KEY) ?? "[]");
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  const batches: StoredBatch[] = [];
  for (const item of parsed as unknown[]) {
    if (typeof item !== "object" || item === null) continue;
    const { id, at, already, notSent } = item as Record<string, unknown>;
    const when = whole(at);
    const kept = { already: whole(already), notSent: whole(notSent) };
    if (typeof id !== "string" || !UUID.test(id) || when === null || now - when >= KEEP_MS || when > now) continue;
    if (kept.already === null || kept.notSent === null) continue;
    batches.push({ id: id.toLowerCase(), at: when, already: kept.already, notSent: kept.notSent });
  }
  return batches.sort((a, b) => b.at - a.at);
}

/** Remember these drops (only those of the last 24 hours); a store that throws only means they are not kept. */
export function writeStored(store: Store | null, batches: readonly StoredBatch[], now: number): void {
  const kept = batches.filter((batch) => now - batch.at < KEEP_MS);
  try {
    if (kept.length === 0) store?.removeItem(STORE_KEY);
    else store?.setItem(STORE_KEY, JSON.stringify(kept));
  } catch {
    // not remembered; the tray still works on this page
  }
}

/** Forget every drop (signing out). */
export function clearStored(store: Store | null = batchStore()): void {
  try {
    store?.removeItem(STORE_KEY);
  } catch {
    // nothing kept, or nothing to clear
  }
}

// --- auto-filing (feature 28) -------------------------------------------------------

export const NOT_FILED_TEXT =
  "Not filed. Kaagaz couldn't tell which folder this belongs in. You can move it to a folder.";
export const UNDONE_TEXT = "Moved back. It is not in a folder now. The folder stays.";
export const FILED_HERE_TEXT = "Filed here by Kaagaz";

/** "Filed into Invoices" with the folder's path; "a folder" while the folders are not loaded yet. */
export function filedText(path: string): string {
  return `Filed into ${path || "a folder"}`;
}

/** What a refused or failed Undo says: the server's plain words, else a hint to try again. */
export function undoErrorText(caught: unknown): string {
  return caught instanceof ApiError ? caught.message : "Could not undo it. Check your connection, then try again.";
}
