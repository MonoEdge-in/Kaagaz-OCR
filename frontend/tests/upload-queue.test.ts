// Uploads in parallel (PRD 5 Q2): 3 files at a time, never more; a duplicate is still
// refused per file and shown as "Already uploaded"; a stored file is followed while it is
// read (U10), so each row's state, in words, covers every step.

import { describe, expect, it } from "vitest";
import { uploadStateText } from "@/components/documents/logic";
import type { UploadState } from "@/components/documents/logic";
import {
  EMPTY_QUEUE,
  UPLOADS_AT_ONCE,
  followed,
  isBusy,
  createSlots,
  queueReducer,
} from "@/components/documents/uploadQueue";
import type { UploadEntry } from "@/components/documents/uploadQueue";
import type { DocumentSummary } from "@/lib/types";

const doc = (id: string, more: Partial<DocumentSummary> = {}): DocumentSummary => ({
  id,
  display_name: `${id}.pdf`,
  detected_type: "pdf",
  size_bytes: 1000,
  status: "clean",
  failure_code: null,
  status_message: "Reading page 1 of 2…",
  page_count: 2,
  created_at: "2026-10-09T10:00:00Z",
  uploaded_by_name: null,
  folder_id: null,
  tags: [],
  deleted_at: null,
  version_no: 1,
  family_id: id,
  can_delete: true,
  attempts: 1,
  max_attempts: 5,
  newer_version: null,
  progress: { step: "reading", done: 0, total: 2 },
  time_left: null,
  doc_type: null,
  languages: [],
  pages_unreadable: 0,
  pages_uncertain: 0,
  values_to_review: 0,
  ...more,
});

const entry = (key: string, state: UploadState, batchId = "b1"): UploadEntry => ({
  key,
  name: `${key}.pdf`,
  size: 1000,
  batchId,
  state,
});

// Changed on purpose (Vrushit UAT, 25 Oct 2026): runLimited ran 3 at a time per drop, so
// several drops sent 3 each; createSlots is one limit shared by every drop.
describe("3 at a time (Q2)", () => {
  it("never has more than 3 uploads going at once, across drops, and sends every file in order", async () => {
    const slots = createSlots(UPLOADS_AT_ONCE);
    let going = 0;
    let most = 0;
    const started: number[] = [];
    const releases: (() => void)[] = [];
    const work = (n: number) =>
      slots(async () => {
        started.push(n);
        going += 1;
        most = Math.max(most, going);
        await new Promise<void>((resolve) => releases.push(resolve));
        going -= 1;
      });
    // Three drops, as a person adds files in several goes, each started before the last ends.
    const run = Promise.all([[1, 2], [3, 4, 5], [6, 7]].map((drop) => Promise.all(drop.map(work))));
    for (let i = 0; i < 7; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(going).toBeLessThanOrEqual(3);
      releases.shift()?.();
    }
    await run;
    expect(UPLOADS_AT_ONCE).toBe(3);
    expect(most).toBe(3);
    expect(started).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  it("frees the slot when a file fails, and gives back what the work gave", async () => {
    const slots = createSlots(1);
    await expect(slots(async () => Promise.reject(new Error("refused")))).rejects.toThrow("refused");
    await expect(slots(async () => "a")).resolves.toBe("a");
  });
});

describe("each state in words", () => {
  it("says every step of a file", () => {
    expect(uploadStateText({ kind: "waiting" })).toBe("Waiting to upload");
    // Changed on purpose (scan states, 16 Oct 2026): the brief's words, Uploading then Scanning.
    expect(uploadStateText({ kind: "sending", fraction: 0.4 })).toBe("Uploading");
    expect(uploadStateText({ kind: "saving" })).toBe("Saving");
    expect(uploadStateText({ kind: "reading", documentId: "d1", document: doc("d1") })).toBe("Scanning");
    expect(uploadStateText({ kind: "done", documentId: "d1" })).toBe("Uploaded");
    expect(uploadStateText({ kind: "done", documentId: "d1", document: doc("d1", { status: "ready" }) })).toBe(
      "Ready",
    );
    expect(uploadStateText({ kind: "done", documentId: "d1", document: doc("d1", { status: "failed" }) })).toBe(
      "Not read",
    );
    expect(uploadStateText({ kind: "error", message: "No.", retry: true })).toBe("Not sent");
    expect(uploadStateText({ kind: "error", message: "Too big." })).toBe("Not uploaded");
  });

  it("shows a duplicate as Already uploaded", () => {
    const duplicate: UploadState = { kind: "duplicate", documentId: "d0", existingName: "bill.pdf", inTrash: false };
    expect(uploadStateText(duplicate)).toBe("Already uploaded");
    expect(isBusy(entry("a", duplicate))).toBe(false);
  });
});

describe("following a stored file (U10)", () => {
  it("is reading while the document is checked or read, then done with it", () => {
    expect(followed(doc("d1"))).toEqual({ kind: "reading", documentId: "d1", document: doc("d1") });
    expect(followed(doc("d1", { status: "received", progress: null })).kind).toBe("reading");
    const ready = doc("d1", { status: "ready", progress: null });
    expect(followed(ready)).toEqual({ kind: "done", documentId: "d1", document: ready });
  });

  it("keeps a row busy until its document is read, so the batch settles only then", () => {
    let queue = queueReducer(EMPTY_QUEUE, { type: "add", entries: [entry("a", { kind: "waiting" })] });
    queue = queueReducer(queue, { type: "change", key: "a", change: { state: followed(doc("d1")) } });
    expect(isBusy(queue.entries[0]!)).toBe(true);
    expect(queue.finished).toBeNull();
    const ready = doc("d1", { status: "ready", progress: null });
    queue = queueReducer(queue, { type: "batch", batchId: "b1", documents: [ready], add: false });
    expect(queue.entries[0]?.state).toEqual({ kind: "done", documentId: "d1", document: ready });
    expect(queue.finished).toEqual({ uploaded: 1, already: 0, failed: 0 });
  });

  it("adds a row for each other file of a drop from before a reload, and none for a live drop", () => {
    const queue = queueReducer(EMPTY_QUEUE, {
      type: "add",
      entries: [entry("a", { kind: "reading", documentId: "d1", document: doc("d1") })],
    });
    const live = queueReducer(queue, { type: "batch", batchId: "b1", documents: [doc("d1"), doc("d2")], add: false });
    expect(live.entries.map((row) => row.key)).toEqual(["a"]);
    const after = queueReducer(queue, { type: "batch", batchId: "b1", documents: [doc("d1"), doc("d2")], add: true });
    expect(after.entries.map((row) => row.key)).toEqual(["a", "document-d2"]);
    expect(after.entries[1]).toMatchObject({ name: "d2.pdf", size: 1000, pages: 2, batchId: "b1" });
  });
});

describe("removing a row not uploaded (PRD 9 F-04)", () => {
  it("takes out that failed or refused row only, and leaves every other row", () => {
    const queue = {
      ...EMPTY_QUEUE,
      entries: [
        entry("refused", { kind: "error", message: "Kaagaz can’t read .docm files." }),
        entry("lost", { kind: "error", message: "Not sent.", retry: true }),
        entry("sent", { kind: "done", documentId: "d1" }),
        entry("going", { kind: "sending", fraction: 0.5 }),
      ],
    };
    const once = queueReducer(queue, { type: "remove", key: "refused" });
    expect(once.entries.map((row) => row.key)).toEqual(["lost", "sent", "going"]);
    const twice = queueReducer(once, { type: "remove", key: "lost" });
    expect(twice.entries.map((row) => row.key)).toEqual(["sent", "going"]);
    for (const key of ["sent", "going"]) expect(queueReducer(twice, { type: "remove", key }).entries).toEqual(twice.entries);
  });
});
