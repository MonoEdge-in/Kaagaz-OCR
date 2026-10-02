import { describe, expect, it } from "vitest";
import {
  shouldReloadAfterUploads,
  uploadErrorText,
  uploadStateText,
  uploadsFinishedText,
} from "@/components/documents/logic";
import type { UploadState } from "@/components/documents/logic";
import { EMPTY_QUEUE, queueReducer } from "@/components/documents/uploadQueue";
import type { UploadEntry } from "@/components/documents/uploadQueue";
import { ApiError } from "@/lib/api";

const done: UploadState = { kind: "done", documentId: "d1" };
const duplicate = (inTrash: boolean): UploadState => ({
  kind: "duplicate",
  documentId: "d2",
  existingName: "bill.pdf",
  inTrash,
});

describe("the words for a duplicate that is in Trash", () => {
  it("says it is in Trash, then that it is restored", () => {
    expect(uploadStateText(duplicate(false))).toBe("Already uploaded");
    expect(uploadStateText(duplicate(true))).toBe("Already uploaded, in Trash");
    expect(
      uploadStateText({ kind: "restoring", documentId: "d2", existingName: "bill.pdf" }),
    ).toBe("Restoring");
    expect(
      uploadStateText({ kind: "restored", documentId: "d2", existingName: "bill.pdf" }),
    ).toBe("Restored from Trash");
  });
});

describe("shouldReloadAfterUploads", () => {
  it("waits until every upload has finished, and something new came of it", () => {
    expect(shouldReloadAfterUploads([])).toBe(false);
    expect(shouldReloadAfterUploads([done, { kind: "uploading", fraction: 0.5 }])).toBe(false);
    expect(shouldReloadAfterUploads([done, { kind: "waiting" }])).toBe(false);
    expect(shouldReloadAfterUploads([done])).toBe(true);
    expect(shouldReloadAfterUploads([duplicate(false)])).toBe(true);
    expect(shouldReloadAfterUploads([{ kind: "error", message: "No." }])).toBe(false);
  });

  it("keeps the page while a duplicate in Trash waits for Restore", () => {
    expect(shouldReloadAfterUploads([done, duplicate(true)])).toBe(false);
    expect(
      shouldReloadAfterUploads([{ kind: "restoring", documentId: "d2", existingName: "bill.pdf" }]),
    ).toBe(false);
    expect(
      shouldReloadAfterUploads([{ kind: "restored", documentId: "d2", existingName: "bill.pdf" }]),
    ).toBe(true);
  });
});

// A8a: the reload after a batch is gone (#35), and nothing is cleared when a
// batch settles: an error or a duplicate stays next to its file until Clear.
describe("the upload queue", () => {
  const entry = (key: string, state: UploadState): UploadEntry => ({ key, name: `${key}.pdf`, size: 10, state });
  const waiting: UploadState = { kind: "waiting" };
  const failed: UploadState = { kind: "error", message: "This file is too large." };
  const restored: UploadState = { kind: "restored", documentId: "d3", existingName: "old.pdf" };

  function settledBatch() {
    let queue = queueReducer(EMPTY_QUEUE, {
      type: "add",
      entries: ["a", "b", "c", "d"].map((key) => entry(key, waiting)),
    });
    queue = queueReducer(queue, { type: "change", key: "a", change: { state: done } });
    queue = queueReducer(queue, { type: "change", key: "b", change: { state: duplicate(false) } });
    queue = queueReducer(queue, { type: "change", key: "c", change: { state: failed } });
    expect(queue.finished).toBeNull(); // d is still waiting
    return queueReducer(queue, { type: "change", key: "d", change: { state: done } });
  }

  it("keeps the error and duplicate rows, and every other row, once a batch settles", () => {
    const queue = settledBatch();
    expect(queue.entries.map((e) => [e.key, e.state.kind])).toEqual([
      ["a", "done"],
      ["b", "duplicate"],
      ["c", "error"],
      ["d", "done"],
    ]);
    expect(queue.finished).toEqual({ uploaded: 2, already: 1, failed: 1 });
    expect(queue.settled).toBe(1);
  });

  it("clears the earlier Uploaded and Restored rows when a new batch starts, never an error or a duplicate", () => {
    let queue = settledBatch();
    queue = queueReducer(queue, { type: "change", key: "b", change: { state: restored } });
    expect(queue.settled).toBe(1); // a Restore is not a batch
    queue = queueReducer(queue, {
      type: "add",
      entries: [entry("e", waiting), entry("f", duplicate(true))],
    });
    expect(queue.entries.map((e) => e.key)).toEqual(["e", "f", "c"]);
    expect(queue.finished).toBeNull();
  });

  it("adds to the running batch while one is still going, and says the whole batch once", () => {
    let queue = queueReducer(EMPTY_QUEUE, { type: "add", entries: [entry("a", waiting)] });
    queue = queueReducer(queue, { type: "change", key: "a", change: { state: done } });
    queue = queueReducer(queue, { type: "add", entries: [entry("b", waiting)] });
    expect(queue.entries.map((e) => e.key)).toEqual(["b"]);
    queue = queueReducer(queue, { type: "add", entries: [entry("c", waiting)] });
    queue = queueReducer(queue, { type: "change", key: "b", change: { state: done } });
    expect(queue.finished).toBeNull();
    queue = queueReducer(queue, { type: "change", key: "c", change: { state: failed } });
    expect(queue.entries.map((e) => e.key)).toEqual(["c", "b"]);
    expect(queue.finished).toEqual({ uploaded: 1, already: 0, failed: 1 });
    expect(queue.settled).toBe(2);
  });

  it("Clear finished keeps only what is still going, and forgets the summary", () => {
    let queue = queueReducer(settledBatch(), { type: "add", entries: [entry("e", waiting)] });
    queue = queueReducer(queue, { type: "clear" });
    expect(queue.entries.map((e) => e.key)).toEqual(["e"]);
    expect(queue.finished).toBeNull();
  });
});

describe("uploadsFinishedText", () => {
  it("says the batch once, in the rows' own words", () => {
    expect(uploadsFinishedText({ uploaded: 5, already: 1, failed: 1 }, false)).toBe(
      "Uploads finished: 5 uploaded, 1 already uploaded, 1 not uploaded.",
    );
    expect(uploadsFinishedText({ uploaded: 2, already: 0, failed: 0 }, false)).toBe(
      "Uploads finished: 2 uploaded.",
    );
  });

  it("asks to clear the search or filters when they may hide the new documents", () => {
    expect(uploadsFinishedText({ uploaded: 1, already: 0, failed: 1 }, true)).toBe(
      "Uploads finished: 1 uploaded, 1 not uploaded. Clear search or filters to see them.",
    );
    expect(uploadsFinishedText({ uploaded: 0, already: 0, failed: 2 }, true)).toBe(
      "Uploads finished: 2 not uploaded.",
    );
  });
});

describe("uploadErrorText", () => {
  it("says to choose the file again once back online", () => {
    const offline = "Not uploaded: no internet connection. Choose the file again when you're back online.";
    expect(uploadErrorText(new ApiError(0, "network", "Could not reach the server."), false)).toBe(offline);
    expect(uploadErrorText(new TypeError("Failed to fetch"), false)).toBe(offline);
    expect(uploadErrorText(new ApiError(413, "too_large", "Too large."), true)).toBe("Too large.");
    expect(uploadErrorText(new TypeError("Failed to fetch"), true)).toBe(
      "Upload failed. Check your connection, then try again.",
    );
  });
});
