// The upload queue's steps, kept out of UploadArea.tsx so they are unit tested
// in node (tests/upload-trash.test.ts). Nothing is cleared when a batch
// settles: an error or a duplicate stays next to its file until "Clear
// finished". When a new batch starts, the earlier Uploaded and Restored rows
// go; their documents are in the list by then. Each batch is summed up once,
// when its last file settles (finished, counted by settled). A stored file is
// followed while it is read (PRD 5 U10), so its batch settles once all are read.
// Files go 3 at a time in all (Q2, createSlots).

import { beingRead } from "@/components/viewer/reprocess";
import { ApiError } from "@/lib/api";
import { POLL_STEPS_MS } from "@/lib/poll";
import type { BatchView, DocumentSummary, UploadReading } from "@/lib/types";
import type { UploadState } from "./logic";
import type { StageTimes } from "./uploadStages";

/** PRD 5 Q2: files sent at once, in all. Their calls are paced too (lib/api.ts, UPLOAD_CALLS_PER_MINUTE). */
export const UPLOADS_AT_ONCE = 3;

export interface UploadEntry {
  key: string;
  name: string;
  size: number;
  versionOf?: string; // for a new version: the name of the document it updates
  pages?: number | null; // a PDF's or TIFF's pages, counted just before sending; null when not known
  reading?: UploadReading; // the reading chosen for it, when not the server's default
  batchId?: string; // the drop it came in (PRD 5 Q1); a new version has its own
  times?: StageTimes; // when it started sending, sent its last byte, and was replied to (U10)
  state: UploadState;
}

export interface UploadCounts {
  uploaded: number;
  already: number; // a duplicate, or one restored from Trash
  failed: number;
}

export interface UploadQueue {
  entries: UploadEntry[];
  batch: string[]; // the keys added since the last batch settled
  finished: UploadCounts | null; // the batch that settled last, until the next starts or Clear
  settled: number; // how many batches have settled
  filed: number; // #536: files seen filed by Kaagaz (feature 28), which may have made a folder for them
}

export const EMPTY_QUEUE: UploadQueue = { entries: [], batch: [], finished: null, settled: 0, filed: 0 };

export type QueueEvent =
  | { type: "add"; entries: UploadEntry[] }
  | { type: "change"; key: string; change: Partial<Omit<UploadEntry, "key">> }
  // A batch's documents as GET /api/batches/{id} gave them: each file's own row follows
  // its document; with add (a drop from before a reload), the others get rows too.
  | { type: "batch"; batchId: string; documents: DocumentSummary[]; add: boolean }
  | { type: "clear" }
  // PRD 9 F-04: one failed or refused row taken out (its Remove button); any other row stays.
  | { type: "remove"; key: string };

export const isBusy = (entry: UploadEntry) =>
  entry.state.kind === "waiting" ||
  entry.state.kind === "sending" ||
  entry.state.kind === "offline" ||
  entry.state.kind === "saving" ||
  entry.state.kind === "reading" ||
  entry.state.kind === "restoring";

/** PRD 9 F-04: the files a pick left out (folderPick's isSkipped), said once in the status line; "" for none. */
export function skippedText(names: readonly string[]): string {
  if (names.length === 0) return "";
  const files = names.length === 1 ? "hidden or system file" : "hidden or system files";
  return `Skipped ${names.length} ${files} (${names.slice(0, 3).join(", ")}${names.length > 3 ? ", …" : ""}).`;
}

/** #468: a file still in this tab only (waiting or sending): leaving the page now would lose it. */
export const uploadsAtRisk = (entries: readonly Pick<UploadEntry, "state">[]) =>
  entries.some((entry) => ["waiting", "sending", "offline", "saving"].includes(entry.state.kind));

/** A stored document's row state: reading while it is checked or read, then done. */
export function followed(document: DocumentSummary): UploadState {
  return beingRead(document.status)
    ? { kind: "reading", documentId: document.id, document }
    : { kind: "done", documentId: document.id, document };
}

const documentOf = (state: UploadState) =>
  state.kind === "reading" || state.kind === "done" ? state.documentId : null;

/** Feature 28: read, then filed by Kaagaz, maybe into a folder it made just then. */
const isFiled = (state: UploadState) => state.kind === "done" && state.document?.autofile === "filed";

const isDoneBefore = (entry: UploadEntry) =>
  entry.state.kind === "done" || entry.state.kind === "restored";

export function queueReducer(queue: UploadQueue, event: QueueEvent): UploadQueue {
  switch (event.type) {
    case "add": {
      const fresh = !queue.entries.some(isBusy);
      const kept = fresh ? queue.entries.filter((entry) => !isDoneBefore(entry)) : queue.entries;
      return settle({
        ...queue,
        entries: [...event.entries, ...kept],
        batch: [...(fresh ? [] : queue.batch), ...event.entries.map((entry) => entry.key)],
        finished: null,
      });
    }
    case "change":
      return settle({
        ...queue,
        entries: queue.entries.map((entry) =>
          entry.key === event.key ? { ...entry, ...event.change } : entry,
        ),
      });
    case "batch": {
      const byId = new Map(event.documents.map((document) => [document.id, document]));
      const seen = new Set<string>();
      let filed = 0;
      const entries = queue.entries.map((entry) => {
        const id = documentOf(entry.state);
        const document = id === null ? undefined : byId.get(id);
        if (!document) return entry;
        seen.add(document.id);
        const state = followed(document);
        if (isFiled(state) && !isFiled(entry.state)) filed += 1;
        return { ...entry, state };
      });
      const added = event.add
        ? event.documents
            .filter((document) => !seen.has(document.id))
            .map((document) => ({
              key: `document-${document.id}`,
              name: document.display_name,
              size: document.size_bytes,
              pages: document.page_count,
              batchId: event.batchId,
              state: followed(document),
            }))
        : [];
      return settle({ ...queue, entries: [...entries, ...added], filed: queue.filed + filed });
    }
    case "clear":
      return { ...queue, entries: queue.entries.filter(isBusy), finished: null };
    case "remove":
      return {
        ...queue,
        entries: queue.entries.filter((entry) => entry.key !== event.key || entry.state.kind !== "error"),
      };
  }
}

/**
 * At most ``limit`` pieces of work at once, across every caller, first come first served
 * (Q2: 3 uploads at a time in all, not per drop: Vrushit UAT, 25 Oct 2026, had several
 * drops sending 3 each). O(1) a call.
 */
export function createSlots(limit: number): <T>(work: () => Promise<T>) => Promise<T> {
  let going = 0;
  const waiting: (() => void)[] = [];
  return async (work) => {
    if (going >= limit) await new Promise<void>((resolve) => waiting.push(resolve));
    else going += 1;
    try {
      return await work();
    } finally {
      const next = waiting.shift();
      if (next) next();
      else going -= 1;
    }
  };
}

/** What one round of following the batches brought back. */
export interface Followed {
  views: BatchView[];
  gone: string[]; // 404: not this person's batch, or none of its files was stored
  failed: boolean; // a batch did not answer (no connection, or a server error): ask again
  skipped: boolean; // another followed batch waits for a later round
}

/**
 * One round of following the drops still being read: one request, for the batch asked
 * longest ago (``asked``: the round each was last asked in; never asked goes first), so
 * following many drops costs one request a round, not one each (Vrushit UAT, 25 Oct 2026:
 * one GET per drop every few seconds used up the API's budget). A 429 is thrown for the
 * poller to wait out (lib/poll.ts).
 */
export async function askBatches(
  following: readonly string[],
  asked: Map<string, number>,
  load: (id: string) => Promise<BatchView>,
): Promise<Followed> {
  const round: Followed = { views: [], gone: [], failed: false, skipped: following.length > 1 };
  const id = [...following].sort((a, b) => (asked.get(a) ?? -1) - (asked.get(b) ?? -1))[0];
  if (id === undefined) return round;
  asked.set(id, Math.max(0, ...asked.values()) + 1);
  try {
    round.views.push(await load(id));
  } catch (caught) {
    if (caught instanceof ApiError && caught.status === 404) round.gone.push(id);
    else if (caught instanceof ApiError && caught.status === 429) throw caught;
    // Asked again only when it may answer next time (review of #564): a refusal (400, 403, 409)
    // or a fault in this page would fail the same way every round, for ever.
    else if (caught instanceof ApiError ? caught.status === 0 || caught.status >= 500 : caught instanceof TypeError) {
      round.failed = true;
    }
  }
  return round;
}

/** How long the screens wait to hear of stored files, so a run of them is one refresh: the
 * poller's first step, so a bulk upload never asks for the list more often than it polls. */
export const NOTIFY_MS = POLL_STEPS_MS[0];

/** Once nothing of the batch is still going, count it. */
function settle(queue: UploadQueue): UploadQueue {
  if (queue.batch.length === 0 || queue.entries.some(isBusy)) return queue;
  const counts: UploadCounts = { uploaded: 0, already: 0, failed: 0 };
  for (const entry of queue.entries) {
    if (!queue.batch.includes(entry.key)) continue;
    if (entry.state.kind === "done") counts.uploaded += 1;
    else if (entry.state.kind === "error") counts.failed += 1;
    else counts.already += 1;
  }
  return { ...queue, batch: [], finished: counts, settled: queue.settled + 1 };
}
