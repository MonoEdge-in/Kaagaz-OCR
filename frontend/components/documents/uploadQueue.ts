// The upload queue's steps, kept out of UploadArea.tsx so they are unit tested
// in node (tests/upload-trash.test.ts). Nothing is cleared when a batch
// settles: an error or a duplicate stays next to its file until "Clear
// finished". When a new batch starts, the earlier Uploaded and Restored rows
// go; their documents are in the list by then. Each batch is summed up once,
// when its last file settles (finished, counted by settled).

import type { UploadState } from "./logic";

export interface UploadEntry {
  key: string;
  name: string;
  size: number;
  versionOf?: string; // for a new version: the name of the document it updates
  pages?: number | null; // a PDF's or TIFF's pages, counted just before sending; null when not known
  ocrChoice?: string; // the scan reader chosen for it, when not the server's default
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
  | { type: "clear" };

export const isBusy = (entry: UploadEntry) =>
  entry.state.kind === "waiting" ||
  entry.state.kind === "uploading" ||
  entry.state.kind === "restoring";

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
    case "clear":
      return { ...queue, entries: queue.entries.filter(isBusy), finished: null };
  }
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
