// The upload queue's steps, kept out of UploadArea.tsx so they are unit tested
// in node (tests/upload-trash.test.ts). Nothing is cleared when a batch
// settles: an error or a duplicate stays next to its file until "Clear
// finished". When a new batch starts, the earlier Uploaded and Restored rows
// go; their documents are in the list by then. Each batch is summed up once,
// when its last file settles (finished, counted by settled). A stored file is
// followed while it is read (PRD 5 U10), so its batch settles once all are read.
// Files go 3 at a time (Q2, runLimited).

import { beingRead } from "@/components/viewer/reprocess";
import type { DocumentSummary, UploadReading } from "@/lib/types";
import type { UploadState } from "./logic";
import type { StageTimes } from "./uploadStages";

/** PRD 5 Q2: files sent at once; 50 files stay well inside RATE_LIMIT_PER_MINUTE. */
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
}

export const EMPTY_QUEUE: UploadQueue = { entries: [], batch: [], finished: null, settled: 0 };

export type QueueEvent =
  | { type: "add"; entries: UploadEntry[] }
  | { type: "change"; key: string; change: Partial<Omit<UploadEntry, "key">> }
  // A batch's documents as GET /api/batches/{id} gave them: each file's own row follows
  // its document; with add (a drop from before a reload), the others get rows too.
  | { type: "batch"; batchId: string; documents: DocumentSummary[]; add: boolean }
  | { type: "clear" };

export const isBusy = (entry: UploadEntry) =>
  entry.state.kind === "waiting" ||
  entry.state.kind === "sending" ||
  entry.state.kind === "saving" ||
  entry.state.kind === "reading" ||
  entry.state.kind === "restoring";

/** A stored document's row state: reading while it is checked or read, then done. */
export function followed(document: DocumentSummary): UploadState {
  return beingRead(document.status)
    ? { kind: "reading", documentId: document.id, document }
    : { kind: "done", documentId: document.id, document };
}

const documentOf = (state: UploadState) =>
  state.kind === "reading" || state.kind === "done" ? state.documentId : null;

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
      const entries = queue.entries.map((entry) => {
        const id = documentOf(entry.state);
        const document = id === null ? undefined : byId.get(id);
        if (!document) return entry;
        seen.add(document.id);
        return { ...entry, state: followed(document) };
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
      return settle({ ...queue, entries: [...entries, ...added] });
    }
    case "clear":
      return { ...queue, entries: queue.entries.filter(isBusy), finished: null };
  }
}

/** Run work on each item, at most ``limit`` at once, in order (Q2: 3 uploads at a time). */
export async function runLimited<T>(
  items: readonly T[],
  limit: number,
  work: (item: T) => Promise<void>,
): Promise<void> {
  let next = 0;
  const slot = async () => {
    while (next < items.length) {
      const item = items[next] as T;
      next += 1;
      await work(item);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, slot));
}

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
