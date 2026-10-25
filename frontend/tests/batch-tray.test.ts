// The batch tray (PRD 5 U1): one line and one bar for the whole drop, then the files
// in the order they were dropped (scan states, 16 Oct 2026); it comes back after a reload for drops of the last 24 hours, from
// localStorage that holds only batch ids and counts, never a file name.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BatchTray } from "@/components/documents/UploadArea";
import {
  KEEP_MS,
  MAX_FILES_PER_BATCH,
  STORE_KEY,
  inBatches,
  batchFraction,
  clearStored,
  leftText,
  newBatchId,
  readStored,
  rowKind,
  summaryText,
  trayCounts,
  unreadableWords,
  verifiedWords,
  writeStored,
} from "@/components/documents/batchTray";
import type { StoredBatch } from "@/components/documents/batchTray";
import { buildTree } from "@/components/documents/folderTree";
import { estimateIngestSeconds, uploadStateText } from "@/components/documents/logic";
import type { UploadState } from "@/components/documents/logic";
import type { UploadEntry } from "@/components/documents/uploadQueue";
import { NO_TIMES, jobFraction } from "@/components/documents/uploadStages";
import { getBatch } from "@/lib/api";
import { documentFraction, longerThanUsual } from "@/lib/progress";
import { markReceived } from "@/lib/timeLeft";
import type { BatchView, DocumentSummary, TimeLeft as TimeLeftValue } from "@/lib/types";

const ID = "6f1c2d3e-4a5b-4c6d-8e7f-901a2b3c4d5e";
const NOW = 1_800_000_000_000;

const doc = (id: string, more: Partial<DocumentSummary> = {}): DocumentSummary => ({
  id,
  display_name: `${id}.pdf`,
  detected_type: "pdf",
  size_bytes: 1000,
  status: "ready",
  failure_code: null,
  status_message: "Ready. You can ask about it.",
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
  progress: null,
  time_left: null,
  doc_type: null,
  languages: [],
  pages_unreadable: 0,
  pages_uncertain: 0,
  values_to_review: 0,
  ...more,
});

const row = (key: string, state: UploadState, more: Partial<UploadEntry> = {}): UploadEntry => ({
  key,
  name: `${key}.pdf`,
  size: 1000,
  batchId: ID,
  state,
  ...more,
});

const done = (document: DocumentSummary): UploadState => ({ kind: "done", documentId: document.id, document });
const TRAY = {
  entries: [] as UploadEntry[],
  views: {},
  stored: [],
  // Changed on purpose (Tushar, 25 Oct 2026): no "too many files" alert any more, so no tooMany.
  onClearFinished: () => undefined,
  onRestore: () => undefined,
  onRetry: () => undefined,
  tree: buildTree([]),
  onUndone: () => undefined,
  canReread: true,
};
const reading = (document: DocumentSummary): UploadState => ({ kind: "reading", documentId: document.id, document });

class MemoryStore {
  items = new Map<string, string>();
  getItem(key: string) {
    return this.items.get(key) ?? null;
  }
  setItem(key: string, value: string) {
    this.items.set(key, value);
  }
  removeItem(key: string) {
    this.items.delete(key);
  }
}

const throwing = {
  getItem(): string | null {
    throw new Error("blocked");
  },
  setItem() {
    throw new Error("blocked");
  },
  removeItem() {
    throw new Error("blocked");
  },
};

describe("each file's place in the tray", () => {
  const rows = [
    row("ready", done(doc("r"))),
    row("dup", { kind: "duplicate", documentId: "x", existingName: "x.pdf", inTrash: false }),
    row("reading", reading(doc("g", { status: "clean", progress: { step: "reading", done: 1, total: 4 } }))),
    row("waiting", reading(doc("w", { status: "received", progress: null }))),
    row("sending", { kind: "sending", fraction: 0.4 }),
    row("saving", { kind: "saving" }),
    row("attention", done(doc("a", { pages_unreadable: 1 }))),
    row("failed", done(doc("f", { status: "failed", status_message: "None of this file could be read." }))),
    row("notsent", { kind: "error", message: "Could not reach the server.", retry: true }),
  ];

  it("names what each row is now", () => {
    expect(rows.map(rowKind)).toEqual([
      "ready",
      "already",
      "reading",
      "waiting",
      "sending",
      "sending",
      "attention",
      "failed",
      "failed",
    ]);
    expect(rowKind(row("old", { kind: "done", documentId: "d" }))).toBe("ready");
    expect(rowKind(row("w2", { kind: "waiting" }))).toBe("sending");
    const held = doc("h", { status: "received", progress: { step: "waiting", done: 0, total: 0 } });
    expect(rowKind(row("held", reading(held)))).toBe("waiting");
    const scanning = doc("s", { status: "received", progress: { step: "scanning", done: 0, total: 0 } });
    expect(rowKind(row("scan", reading(scanning)))).toBe("reading");
  });

  // Changed on purpose (scan states, 16 Oct 2026): not worst first any more, which moved a
  // file under the pointer each time one changed state; the drop's order, whatever the state.
  it("lists the files in the order they were dropped, whatever their state", () => {
    const html = renderToStaticMarkup(createElement(BatchTray, { ...TRAY, entries: rows }));
    const at = rows.map((item) => html.indexOf(`>${item.key}.pdf<`));
    expect(at.every((place) => place > -1)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
  });

  it("sums the drop up in one line", () => {
    const counts = trayCounts(rows);
    expect(counts).toEqual({
      total: 9,
      ready: 1,
      reading: 1,
      waiting: 1,
      sending: 2,
      attention: 1,
      failed: 2,
      stopped: 0,
      already: 1,
    });
    expect(summaryText(counts)).toBe(
      "Batch of 9 files · 1 ready · 2 sending · 1 reading · 1 waiting · 1 needs attention · 2 failed · 1 already uploaded",
    );
  });

  it("says the PRD's line for a drop of 50", () => {
    const counts = {
      total: 50,
      ready: 31,
      reading: 12,
      waiting: 4,
      sending: 0,
      attention: 2,
      failed: 0,
      stopped: 0,
      already: 1,
    };
    expect(summaryText(counts)).toBe(
      "Batch of 50 files · 31 ready · 12 reading · 4 waiting · 2 need attention · 1 already uploaded",
    );
    expect(summaryText(trayCounts([row("one", done(doc("o")))]))).toBe("Batch of 1 file · 1 ready");
  });

  it("says a file of a disabled organisation is stopped, not failed (PRD 12)", () => {
    const stopped = done(
      doc("s", { status: "failed", failure_code: "customer_disabled", status_message: "This organisation is disabled." }),
    );
    expect(rowKind(row("s", stopped))).toBe("stopped");
    expect(uploadStateText(stopped)).toBe("Stopped");
    const counts = trayCounts([row("s", stopped), row("r", done(doc("r")))]);
    expect(counts).toMatchObject({ total: 2, stopped: 1, failed: 0 });
    expect(summaryText(counts)).toBe("Batch of 2 files · 1 ready · 1 stopped");
  });

  it("names an unreadable file's first page, with the reason and the fix (U1)", () => {
    const page = { page_no: 1, says: "Glare or a shadow covers part of the page.", fix: "Take the photo again." };
    const unreadable = done(doc("u", { pages_unreadable: 1, first_unreadable: page }));
    expect(uploadStateText(unreadable)).toBe("Page 1 to check");
    expect(unreadableWords(doc("u", { pages_unreadable: 1, first_unreadable: page }), true)).toEqual({
      title: "Page 1 to check",
      detail: "Glare or a shadow covers part of the page. Take the photo again.",
    });
    const unsaid = doc("v", { pages_unreadable: 1, first_unreadable: { page_no: 3, says: null, fix: null } });
    expect(unreadableWords(unsaid, true)).toEqual({ title: "Page 3 to check", detail: null });
    expect(unreadableWords(doc("w"), true)).toBeNull(); // none, or a listing without the field
    expect(uploadStateText(done(doc("w")))).toBe("Ready");
  });

  // Council 16 Oct 2026: a member can't Read again (canReprocess), so is never told to.
  it("tells a member to upload a better scan, not to read the page again", () => {
    const page = { page_no: 2, says: "This page needs a closer look.", fix: "Read it again, or upload a better scan." };
    const unreadable = doc("u", { pages_unreadable: 1, first_unreadable: page });
    expect(unreadableWords(unreadable, false)?.detail).toBe("This page needs a closer look. Upload a better scan.");
    expect(unreadableWords(unreadable, true)?.detail).toBe(
      "This page needs a closer look. Read it again, or upload a better scan.",
    );
    const photo = { page_no: 1, says: "Glare or a shadow covers part of the page.", fix: "Take the photo again." };
    expect(unreadableWords(doc("p", { pages_unreadable: 1, first_unreadable: photo }), false)?.detail).toBe(
      "Glare or a shadow covers part of the page. Take the photo again.",
    );
  });

  it("says All values verified only for a ready file with values and nothing to check", () => {
    expect(verifiedWords(doc("v", { values_found: 4 }))).toBe("All values verified");
    expect(verifiedWords(doc("n", { values_found: 0 }))).toBeNull(); // no values: just Ready
    expect(verifiedWords(doc("u"))).toBeNull(); // a listing without the count
    // Changed on purpose (QA issue #366, 25 Oct 2026): a value still waiting is named, so
    // every value shows as Verified or Ready to verify.
    expect(verifiedWords(doc("c", { values_found: 4, values_to_review: 1 }))).toBe("1 value ready to verify");
    expect(verifiedWords(doc("f", { values_found: 4, status: "failed" }))).toBeNull();
  });

  it("never says All values verified while a value read once still waits (QA #366)", () => {
    // The invoice date read by one reader only (single_reader), as the API now counts it.
    const waiting = doc("d", { values_found: 5, values_to_review: 2, words_unsure: 3, pages_uncertain: 1 });
    expect(verifiedWords(waiting)).toBe("2 values ready to verify");
    expect(verifiedWords({ ...waiting, values_to_review: 0 })).toBeNull(); // words left: not all verified yet
  });

  it("keeps a reload's counts the batch API does not have: already uploaded and not sent", () => {
    const counts = trayCounts([row("ready", done(doc("r")))], { already: 2, notSent: 1 });
    expect(counts).toMatchObject({ total: 4, ready: 1, already: 2, failed: 1 });
  });
});

describe("what the tray says out loud", () => {
  // Changed on purpose (scan states, 16 Oct 2026): the status bar's one polite line speaks for
  // the uploads (statusBarText, tests/scan-states.test.ts), so the tray has no live region.
  it("has no live region of its own; the batch lines are shown, not announced", () => {
    const html = renderToStaticMarkup(
      createElement(BatchTray, {
        ...TRAY,
        entries: [
          row("a", done(doc("a"))),
          row("b", { kind: "waiting" }),
          row("c", done(doc("c")), { batchId: "6f1c2d3e-4a5b-4c6d-8e7f-901a2b3c4d5f" }),
        ],
      }),
    );
    expect(html).not.toMatch(/role="status"|aria-live/);
    expect(html).toContain("Batch of 2 files · 1 ready · 1 sending");
  });
});

describe("the drop's bar and time", () => {
  const view: BatchView = {
    id: ID,
    documents: [doc("d1", { status: "clean" })],
    counts: { total: 1, ready: 0, reading: 1, waiting: 0, attention: 0, failed: 0 },
    pages_read: 3,
    pages_total: 6,
    time_left: null,
  };

  // Progress fix, 16 Oct 2026: a document being read, 30 s in with 90 s left by its answer.
  const timeLeft = (more: Partial<TimeLeftValue> = {}): TimeLeftValue => ({
    seconds: 90,
    since_move_seconds: 0,
    slow_after_seconds: 100,
    items_left: 1,
    item_seconds: 7,
    after_seconds: 0,
    confidence: "high",
    elapsed_seconds: 30,
    done_fraction: 0.1,
    ...more,
  });
  const readingDoc = (more: Partial<DocumentSummary> = {}) =>
    doc("d1", { status: "clean", page_count: 6, progress: { step: "reading", done: 0, total: 6 }, ...more });

  it("weighs each stored file's own fraction by its pages; a finished file counts whole", () => {
    // Changed on purpose (progress fix, 16 Oct 2026): not the server's pages_read, which
    // stayed 0 until page 1 was read whole and then jumped; a file not started counts 0.
    expect(batchFraction(view, [row("a", reading(doc("d1", { status: "clean" })))])).toBe(0);
    const ready = doc("d2", { page_count: 2 });
    const busy = readingDoc({ time_left: timeLeft() });
    markReceived([busy], 0);
    const both = { ...view, documents: [busy, ready] };
    expect(batchFraction(both, [], 0)).toBeCloseTo((6 * 0.25 + 2) / 8); // 30 / (30 + 90) live
    expect(batchFraction(null, [])).toBe(0);
  });

  it("is a one-file drop's row and table row exactly: one rule (documentFraction)", () => {
    const busy = readingDoc({ time_left: timeLeft({ done_fraction: 0.2 }) });
    markReceived([busy], 0);
    const entry = row("a", reading(busy), { pages: 6 });
    const one = { ...view, documents: [busy] };
    for (const now of [0, 20_000, 60_000, 500_000]) {
      const table = documentFraction("reading", busy.time_left, now);
      const tray = jobFraction(entry.state, entry.size, 42, NO_TIMES, now);
      expect(tray).toBe(table);
      expect(batchFraction(one, [entry], now)).toBeCloseTo(table ?? -1, 12);
      expect(batchFraction(null, [entry], now)).toBeCloseTo(table ?? -1, 12); // before the batch view has it
    }
    // Past slow_after_seconds with no move it holds the server's done_fraction, all three alike.
    expect(documentFraction("reading", busy.time_left, 500_000)).toBe(0.2);
  });

  it("counts a file not stored yet by its expected pages, and one still sending by its bytes", () => {
    const shown = { ...view, documents: [doc("d1", { page_count: 6 })] }; // read whole: 6 of 12
    const waiting = row("b", { kind: "waiting" }, { pages: 6 });
    expect(batchFraction(shown, [waiting])).toBe(0.5); // none of it read or sent
    const unsent = row("b", { kind: "sending", fraction: 0 }, { pages: 6 });
    expect(batchFraction(shown, [unsent])).toBe(0.5);
    const sending = row("b", { kind: "sending", fraction: 0.9 }, { pages: 6 });
    // Weighed as its own row's bar weighs it (uploadStages.ts): the bytes are part of the job.
    const sent = jobFraction(sending.state, 1000, estimateIngestSeconds("pdf", 1000, 1, 6), NO_TIMES, 0);
    expect(sent).toBeGreaterThan(0);
    expect(batchFraction(shown, [sending])).toBeCloseTo((6 + 6 * sent) / 12);
    expect(batchFraction({ ...view, documents: [] }, [sending])).toBeCloseTo(sent, 12); // a one-file drop: its row
  });

  it("says taking longer than usual by the rows' own rule, never under a minute while stuck", () => {
    const stuck = readingDoc({ time_left: timeLeft({ seconds: 20, since_move_seconds: 90 }) });
    markReceived([stuck], 0);
    const drop = { time_left: stuck.time_left, documents: [stuck] };
    // Changed on purpose (scan states, 16 Oct 2026): "About", whole minutes, and the time with Taking longer.
    expect(leftText(drop, 5_000)).toBe("About 1 min left"); // 95 s since a move: not yet
    expect(leftText(drop, 11_000)).toBe("Taking longer than usual · About 1 min left"); // 101 s, past 100
    expect(longerThanUsual(stuck.time_left!, "reading", 11_000)).toBe(true); // the row says it too
  });

  it("says the time left for the whole drop, or nothing", () => {
    const timeLeft = (seconds: number) => ({
      time_left: {
        seconds,
        since_move_seconds: 0,
        slow_after_seconds: 100,
        items_left: 1,
        item_seconds: 7,
        after_seconds: 0,
        confidence: "high" as const,
        elapsed_seconds: 10,
        done_fraction: 0.2,
      },
      documents: [],
    });
    // Changed on purpose (scan states, 16 Oct 2026): every estimate says "About", in whole minutes.
    expect(leftText(timeLeft(330))).toBe("About 6 min left");
    expect(leftText(timeLeft(20))).toBe("About 1 min left");
    expect(leftText({ time_left: null, documents: [] })).toBeNull();
    expect(leftText(null)).toBeNull();
  });
});

// Changed on purpose (Tushar, 25 Oct 2026): "no limit on the number of documents put in the
// queue". A drop of more than 100 files was cut at 100 with "Up to 100 files at a time"; now
// it is split into batches of 100 (the server's batch size) and every file is queued.
describe("any number of files a drop", () => {
  it("splits a pick of 600 into six batches of 100, every file kept, in order", () => {
    expect(MAX_FILES_PER_BATCH).toBe(100);
    const pick = Array.from({ length: 600 }, (_, i) => i);
    const batches = inBatches(pick);
    expect(batches.map((batch) => batch.length)).toEqual([100, 100, 100, 100, 100, 100]);
    expect(batches.flat()).toEqual(pick);
    expect(inBatches(pick.slice(0, 250)).map((batch) => batch.length)).toEqual([100, 100, 50]);
    expect(inBatches([])).toEqual([]);
  });
});

describe("a batch id per drop", () => {
  it("is a random UUID (v4)", () => {
    const id = newBatchId((bytes) => bytes.fill(0xff));
    expect(id).toBe("ffffffff-ffff-4fff-bfff-ffffffffffff");
    expect(newBatchId()).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(newBatchId()).not.toBe(newBatchId());
  });
});

describe("remembered in this browser", () => {
  const batch: StoredBatch = { id: ID, at: NOW - 1000, already: 1, notSent: 0 };

  it("keeps only ids and counts, never a file name, and reads them back", () => {
    const store = new MemoryStore();
    writeStored(store, [batch], NOW);
    expect(JSON.parse(store.getItem(STORE_KEY) ?? "")).toEqual([batch]);
    expect(Object.keys(batch).sort()).toEqual(["already", "at", "id", "notSent"]);
    expect(readStored(store, NOW)).toEqual([batch]);
  });

  it("forgets drops older than 24 hours, and anything malformed", () => {
    const store = new MemoryStore();
    store.setItem(
      STORE_KEY,
      JSON.stringify([
        batch,
        { ...batch, id: "6f1c2d3e-4a5b-4c6d-8e7f-901a2b3c4d5f", at: NOW - KEEP_MS },
        { ...batch, id: "not-a-uuid" },
        { ...batch, id: "6f1c2d3e-4a5b-4c6d-8e7f-901a2b3c4d60", already: -1 },
        { ...batch, id: "6f1c2d3e-4a5b-4c6d-8e7f-901a2b3c4d61", name: "secret.pdf", at: NOW + 1 },
        "junk",
      ]),
    );
    expect(readStored(store, NOW)).toEqual([batch]);
    store.setItem(STORE_KEY, "{not json");
    expect(readStored(store, NOW)).toEqual([]);
    expect(readStored(null, NOW)).toEqual([]);
  });

  it("lists the newest drop first", () => {
    const store = new MemoryStore();
    const newer = { ...batch, id: "6f1c2d3e-4a5b-4c6d-8e7f-901a2b3c4d5f", at: NOW - 10 };
    writeStored(store, [batch, newer], NOW);
    expect(readStored(store, NOW).map((item) => item.id)).toEqual([newer.id, batch.id]);
  });

  it("removes the key once nothing is left, and on sign-out", () => {
    const store = new MemoryStore();
    writeStored(store, [batch], NOW);
    writeStored(store, [], NOW);
    expect(store.getItem(STORE_KEY)).toBeNull();
    writeStored(store, [batch], NOW);
    clearStored(store);
    expect(store.getItem(STORE_KEY)).toBeNull();
  });

  it("works on when the browser blocks storage", () => {
    expect(readStored(throwing, NOW)).toEqual([]);
    expect(() => writeStored(throwing, [batch], NOW)).not.toThrow();
    expect(() => clearStored(throwing)).not.toThrow();
  });

  // Changed on purpose (UI audit G6, 16 Oct 2026): the sign-outs moved to the header's account menu.
  it("is forgotten when signing out (AccountMenu)", () => {
    const shell = readFileSync(join(process.cwd(), "components/shell/AccountMenu.tsx"), "utf8");
    expect(shell).toMatch(/clearStored\(\)/);
  });
});

describe("GET /api/batches/{id}", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("asks for the batch by its id", async () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      urls.push(url);
      return new Response(JSON.stringify({ id: ID, documents: [] }), { status: 200 });
    });
    await expect(getBatch(ID)).resolves.toMatchObject({ id: ID });
    expect(urls).toEqual([`/api/batches/${ID}`]);
  });
});
