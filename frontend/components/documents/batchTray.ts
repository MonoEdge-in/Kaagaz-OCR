// The batch tray (PRD 5 U1), kept pure so it is unit tested in node
// (tests/batch-tray.test.ts): one line and one bar for each drop of files, then its
// files, worst first. A drop is remembered in this browser for 24 hours so the tray
// comes back after a reload (from GET /api/batches/{id}): only its id and two counts
// the server does not keep (files refused as already uploaded, and files not sent),
// never a file name. Signing out forgets them (AppShell). Every storage access is
// wrapped: blocked site data only means the tray does not come back.

import type { BatchView } from "@/lib/types";
import { attentionWords } from "./library";
import { estimatePages, uploadKind } from "./logic";
import type { UploadEntry } from "./uploadQueue";

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

export type RowKind = "failed" | "attention" | "waiting" | "sending" | "reading" | "already" | "ready";

const WORST_FIRST: readonly RowKind[] = ["failed", "attention", "waiting", "sending", "reading", "already", "ready"];

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
      const step = state.document.progress?.step;
      return step === undefined || step === "scanning" ? "waiting" : "reading";
    }
    case "done":
      if (!state.document) return "ready";
      if (state.document.status !== "ready") return "failed";
      return attentionWords(state.document).length > 0 ? "attention" : "ready";
  }
}

/** The files worst first: failed, need attention, waiting, sending, reading, already uploaded, ready. */
export function worstFirst<T extends Pick<UploadEntry, "state">>(entries: readonly T[]): T[] {
  return [...entries].sort((a, b) => WORST_FIRST.indexOf(rowKind(a)) - WORST_FIRST.indexOf(rowKind(b)));
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
  already: number;
}

/** The counts of one drop's rows; ``stored`` adds what a reload lost (already uploaded, not sent). */
export function trayCounts(
  entries: readonly Pick<UploadEntry, "state">[],
  stored: { already: number; notSent: number } = { already: 0, notSent: 0 },
): TrayCounts {
  const counts: TrayCounts = { total: 0, ready: 0, reading: 0, waiting: 0, sending: 0, attention: 0, failed: 0, already: 0 };
  for (const entry of entries) counts[rowKind(entry)] += 1;
  counts.already = Math.max(counts.already, stored.already);
  counts.failed = Math.max(counts.failed, stored.notSent);
  counts.total = WORST_FIRST.reduce((sum, kind) => sum + counts[kind], 0);
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
    counts.already > 0 ? `${counts.already} already uploaded` : "",
  ];
  return parts.filter(Boolean).join(" · ");
}

/** "about 6 min left" for the whole drop; null when nothing is left to read or it is not known. */
export function leftText(view: Pick<BatchView, "time_left"> | null): string | null {
  const seconds = view?.time_left?.seconds;
  if (seconds === undefined || !Number.isFinite(seconds) || seconds <= 0) return null;
  return seconds < 60 ? "under a minute left" : `about ${Math.ceil(seconds / 60)} min left`;
}

/**
 * The drop's bar, 0 to 1: pages read over all its pages (the batch API's), with each
 * file not stored yet counted by its expected pages, none of them read.
 */
export function batchFraction(
  view: Pick<BatchView, "pages_read" | "pages_total" | "documents"> | null,
  entries: readonly Pick<UploadEntry, "state" | "name" | "size" | "pages">[],
): number {
  const stored = new Set(view?.documents.map((document) => document.id) ?? []);
  let total = view?.pages_total ?? 0;
  for (const entry of entries) {
    const { state } = entry;
    const kind = uploadKind(entry.name);
    if (kind === null) continue;
    const waiting = state.kind === "waiting" || state.kind === "sending" || state.kind === "saving";
    const unknown = state.kind === "reading" && !stored.has(state.documentId);
    if (waiting || unknown) total += estimatePages(kind, entry.size, entry.pages ?? null);
  }
  return total > 0 ? Math.min(1, Math.max(0, (view?.pages_read ?? 0) / total)) : 0;
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
