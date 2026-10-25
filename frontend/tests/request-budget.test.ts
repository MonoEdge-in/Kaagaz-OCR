// The request budget (Vrushit UAT, 25 Oct 2026: 805 calls refused with "Too many requests",
// 543 of them the batch tray's reads, during a bulk upload in several drops). The real
// uploader (uploadDocument, its pacing, 3 files at a time in all) and the real pollers
// (createPoller with askBatches for the tray, the documents list) run against a stand-in API
// that limits like kaagaz/api/middleware.py: a token bucket per user of RATE_LIMIT_PER_MINUTE
// (read from .env.example, the documented number; backend tests/test_api_auth.py pins it),
// one for a file's upload calls and one for everything else. 60 files in 11 drops,
// read over several minutes, must never be refused, the reads must stay well under the
// limit, and once all are read nothing is asked any more. A 429 is waited out
// (Retry-After), never hammered, by the uploader and the pollers alike.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { inBatches } from "@/components/documents/batchTray";
import { shouldPoll } from "@/components/documents/logic";
import { NOTIFY_MS, UPLOADS_AT_ONCE, askBatches, createSlots } from "@/components/documents/uploadQueue";
import type { Followed } from "@/components/documents/uploadQueue";
import { beingRead } from "@/components/viewer/reprocess";
import { ApiError, UPLOAD_CALLS_PER_MINUTE, getBatch, listDocuments, uploadDocument } from "@/lib/api";
import { createPoller, progressSignature } from "@/lib/poll";
import type { BatchView, DocumentList, DocumentSummary } from "@/lib/types";

const LIMIT = Number(/^RATE_LIMIT_PER_MINUTE=(\d+)$/m.exec(readFileSync(join(__dirname, "..", "..", ".env.example"), "utf8"))?.[1]);
const UPLOAD_PATH = "/api/documents/upload-sessions";

type Bucket = "upload" | "general";

/** The API's limiter (kaagaz/api/middleware.py RateLimiter): capacity LIMIT, LIMIT a minute. */
class Limiter {
  tokens = LIMIT;
  at = Date.now();
  allow(): number {
    const now = Date.now();
    this.tokens = Math.min(LIMIT, this.tokens + ((now - this.at) * LIMIT) / 60_000);
    this.at = now;
    if (this.tokens >= 1) {
      this.tokens -= 1;
      return 0;
    }
    return Math.max(1, Math.ceil(((1 - this.tokens) * 60) / LIMIT)); // Retry-After, seconds
  }
}

interface Doc {
  id: string;
  batch: string;
  readyAt: number;
}

function stubApi() {
  const start = Date.now();
  const buckets: Record<Bucket, Limiter> = { upload: new Limiter(), general: new Limiter() };
  const sent: { at: number; bucket: Bucket; path: string; status: number; wait: number }[] = [];
  const docs: Doc[] = [];
  const sessions = new Map<string, string>(); // session id -> batch id
  let readerFreeAt = 0; // one document read at a time after another, as a busy reader does

  const summary = (doc: Doc): DocumentSummary =>
    ({
      id: doc.id,
      display_name: `${doc.id}.pdf`,
      status: Date.now() >= doc.readyAt ? "ready" : "received",
      status_message: Date.now() >= doc.readyAt ? "Ready" : "Waiting to be read.",
      progress: null,
      newer_version: null,
    }) as DocumentSummary;

  const refuse = { next: 0, wait: 0 }; // refuse the next calls, saying to wait this long

  function answer(method: string, path: string, body?: string): { status: number; body: unknown; wait?: number } {
    const bucket: Bucket = path.startsWith(UPLOAD_PATH) ? "upload" : "general";
    const forced = refuse.next > 0 ? (refuse.next -= 1) >= 0 : false;
    const wait = forced ? refuse.wait : buckets[bucket].allow();
    const status = wait > 0 ? 429 : 200;
    sent.push({ at: Date.now() - start, bucket, path, status, wait });
    if (wait > 0) return { status, wait, body: { error: { code: "rate_limited", message: "Too many requests." } } };
    if (path === `${UPLOAD_PATH}/candidates`) return { status, body: { candidates: [] } };
    if (path === UPLOAD_PATH) {
      // The API's MAX_FILES_PER_BATCH: the 101st file of one batch is 409 batch_full.
      const batch = String((JSON.parse(body ?? "{}") as { batch_id?: string }).batch_id);
      if ([...sessions.values()].filter((id) => id === batch).length >= 100) {
        return { status: 409, body: { error: { code: "batch_full", message: "Up to 100 files at a time." } } };
      }
      return { status, body: { id: `s${sessions.size + 1}`, part_size: 8 * 1024 * 1024, part_count: 1 } };
    }
    if (/\/parts\/\d+$/.test(path)) return { status, body: {} };
    if (path.endsWith("/complete")) {
      const id = `d${docs.length + 1}`;
      readerFreeAt = Math.max(readerFreeAt, Date.now()) + 6_000;
      const doc = { id, batch: sessions.get(path.split("/")[4] ?? "") ?? "", readyAt: readerFreeAt };
      docs.push(doc);
      return { status: 201, body: { document: summary(doc), duplicate: false, in_trash: false } };
    }
    if (path.startsWith("/api/batches/")) {
      const id = path.slice("/api/batches/".length);
      const documents = docs.filter((doc) => doc.batch === id).map(summary);
      return documents.length ? { status, body: { id, documents } as Partial<BatchView> } : { status: 404, body: { error: { code: "not_found", message: "Not found." } } };
    }
    if (path === "/api/documents" && method === "GET") return { status, body: { items: docs.slice(-50).map(summary), total: docs.length } satisfies DocumentList };
    throw new Error(`unexpected ${method} ${path}`);
  }

  class FakeXhr {
    status = 0;
    responseText = "";
    upload = { onprogress: null as ((event: { lengthComputable: boolean; loaded: number }) => void) | null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onabort: (() => void) | null = null;
    withCredentials = false;
    private path = "";
    private wait = 0;
    open(_method: string, url: string) {
      this.path = url;
    }
    setRequestHeader() {}
    getResponseHeader(name: string) {
      return name === "Retry-After" && this.wait > 0 ? String(this.wait) : null;
    }
    send() {
      const reply = answer("PUT", this.path);
      this.status = reply.status === 200 ? 204 : reply.status;
      this.wait = reply.wait ?? 0;
      this.responseText = JSON.stringify(reply.body);
      queueMicrotask(() => this.onload?.());
    }
  }

  vi.stubGlobal("XMLHttpRequest", FakeXhr);
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const path = url.split("?")[0] ?? url;
    const reply = answer(init.method ?? "GET", path, init.body ? String(init.body) : undefined);
    if (path === UPLOAD_PATH && reply.status === 200) {
      const body = JSON.parse(String(init.body)) as { batch_id: string };
      sessions.set((reply.body as { id: string }).id, body.batch_id);
    }
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (reply.wait) headers["Retry-After"] = String(reply.wait);
    return new Response(JSON.stringify(reply.body), { status: reply.status, headers });
  });
  return { sent, docs, refuse };
}

/** The most requests of one bucket in any 60 s. */
function busiestMinute(sent: { at: number; bucket: Bucket }[], bucket: Bucket): number {
  const times = sent.filter((request) => request.bucket === bucket).map((request) => request.at);
  let most = 0;
  for (let i = 0, j = 0; i < times.length; i += 1) {
    while ((times[i] ?? 0) - (times[j] ?? 0) >= 60_000) j += 1;
    most = Math.max(most, i - j + 1);
  }
  return most;
}

/** How many of one bucket's requests a limiter of `perMinute` (the API's kind) would refuse. */
function refusedUnder(sent: { at: number; bucket: Bucket }[], bucket: Bucket, perMinute: number): number {
  let tokens = perMinute;
  let at = 0;
  let refused = 0;
  for (const request of sent.filter((one) => one.bucket === bucket)) {
    tokens = Math.min(perMinute, tokens + ((request.at - at) * perMinute) / 60_000);
    at = request.at;
    if (tokens >= 1) tokens -= 1;
    else refused += 1;
  }
  return refused;
}

/** Let real work (hashing each file) and the fake timers move on, `ms` of simulated time. */
async function pass(ms: number) {
  for (let left = ms; left > 0; left -= 100) {
    await new Promise((resolve) => setImmediate(resolve));
    vi.advanceTimersByTime(100);
  }
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("the request budget of a bulk upload", () => {
  it("reads the API's documented limit", () => {
    expect(LIMIT).toBeGreaterThan(0);
    expect(UPLOAD_CALLS_PER_MINUTE).toBeLessThan(LIMIT); // the uploader's pace fits its own bucket
  });

  it("uploads 60 files in 11 drops without one refusal, polls well under the limit, then stops", async () => {
    const api = stubApi();
    const slots = createSlots(UPLOADS_AT_ONCE);
    // As UploadArea: a drop is followed while a stored file of it is still being read.
    const following = new Set<string>();
    const asked = new Map<string, number>();
    let failures = 0;

    // The tray (UploadArea): one request a round for the drops still being read.
    const tray = createPoller<Followed>(
      () => ({
        load: () => askBatches([...following], asked, getBatch),
        onResult: (round) => {
          for (const view of round.views) {
            if (!view.documents.some((document) => beingRead(document.status))) following.delete(view.id);
          }
        },
        onFailure: () => (failures += 1),
        again: (round) => round.skipped || round.views.some((view) => view.documents.some((d) => beingRead(d.status))),
        signature: (round) => JSON.stringify(round.views.map((view) => [view.id, progressSignature(view.documents)])),
      }),
      () => false,
    );
    // The documents list (DocumentsView): polled while anything is read, and asked again
    // when files are stored, at most every NOTIFY_MS (components/shell/Uploads.tsx).
    const list = createPoller<DocumentList>(
      () => ({
        load: () => listDocuments(),
        onResult: () => undefined,
        onFailure: () => (failures += 1),
        again: (result) => shouldPoll(result.items),
        signature: (result) => progressSignature(result.items),
      }),
      () => false,
    );
    let notifying = false;
    const notify = () => {
      if (notifying) return;
      notifying = true;
      setTimeout(() => {
        notifying = false;
        list.refresh();
      }, NOTIFY_MS);
    };
    list.refresh();

    const drops = [2, 2, 8, 3, 5, 10, 4, 6, 2, 8, 10];
    expect(drops.reduce((sum, n) => sum + n, 0)).toBe(60);
    const uploads: Promise<unknown>[] = [];
    let fileNo = 0;
    for (const [index, size] of drops.entries()) {
      const batchId = `0000000${index}-0000-4000-8000-000000000000`;
      const sending = Array.from({ length: size }, () => {
        fileNo += 1;
        const file = new File([`%PDF-1.7 scan ${fileNo}`], `scan-${fileNo}.pdf`);
        return slots(() => uploadDocument(file, "u1", () => {}, undefined, undefined, batchId)).then(() => {
          if (!following.has(batchId)) {
            following.add(batchId);
            tray.refresh();
          }
          notify();
        });
      });
      uploads.push(Promise.all(sending));
      await pass(10_000); // the next drop 10 s later, while these are still going
    }
    await pass(12 * 60_000);
    await Promise.all(uploads);

    expect(api.docs).toHaveLength(60);
    expect(api.docs.every((doc) => Date.now() >= doc.readyAt)).toBe(true);
    expect(api.sent.filter((request) => request.status === 429)).toEqual([]); // never refused
    expect(failures).toBe(0);
    // Comfortably under, in the limiter's own terms: the screens' reads would pass a limiter
    // of half the limit, whatever the number of drops (the old one GET per drop a round was
    // 11 x 20 a minute here), and the upload calls one of 70% of it.
    expect(refusedUnder(api.sent, "general", LIMIT / 2)).toBe(0);
    expect(busiestMinute(api.sent, "general")).toBeLessThanOrEqual(LIMIT / 2);
    expect(refusedUnder(api.sent, "upload", LIMIT * 0.7)).toBe(0);

    // All read: nothing is asked any more.
    const before = api.sent.length;
    await pass(3 * 60_000);
    expect(api.sent.length).toBe(before);
    tray.stop();
    list.stop();
  });
});

describe("any number of files may be queued (Tushar, 25 Oct 2026)", () => {
  it("queues a pick of 600 files in batches of 100, none refused for the count or the pace", async () => {
    const api = stubApi();
    const slots = createSlots(UPLOADS_AT_ONCE);
    const errors: unknown[] = [];
    const pick = Array.from({ length: 600 }, (_, i) => new File([`%PDF-1.7 page ${i}`], `deed-${i}.pdf`));
    const uploads = inBatches(pick).flatMap((batch, index) => {
      const batchId = `${String(index).padStart(8, "0")}-0000-4000-8000-000000000000`;
      return batch.map((file) =>
        slots(() => uploadDocument(file, "u1", () => {}, undefined, undefined, batchId)).catch((caught: unknown) => {
          errors.push(caught);
        }),
      );
    });
    await pass(35 * 60_000); // 600 files x 4 calls at UPLOAD_CALLS_PER_MINUTE
    await Promise.all(uploads);
    expect(errors).toEqual([]); // no batch_full, no "Too many requests"
    expect(api.docs).toHaveLength(600);
    expect(api.sent.filter((request) => request.status >= 400)).toEqual([]);
  });
});

describe("a 429 is waited out, never hammered", () => {
  it("the uploader waits the Retry-After, without counting a try or saying the connection is lost", async () => {
    const api = stubApi();
    Object.assign(api.refuse, { next: 3, wait: 4 }); // three calls in a row told to wait 4 s
    const waiting: boolean[] = [];
    let result: unknown = null;
    void uploadDocument(new File(["%PDF-1.7"], "deed.pdf"), "u1", () => {}, undefined, undefined, undefined, undefined, undefined, (w) => waiting.push(w)).then((reply) => (result = reply));
    await pass(30_000);
    expect(result).toMatchObject({ duplicate: false });
    const refused = api.sent.filter((request) => request.status === 429);
    expect(refused).toHaveLength(3); // more than the 4 tries a failing server gets would not matter either
    for (const request of refused) {
      // Told to wait, it waited at least that long before its next call.
      const next = api.sent[api.sent.indexOf(request) + 1]!;
      expect(next.at - request.at).toBeGreaterThanOrEqual(4000);
    }
    expect(api.docs).toHaveLength(1); // stored once
    expect(waiting).toEqual([]); // no "Waiting for connection": the connection was fine
  });

  it("a poll waits the Retry-After and does not tell the screen of a failure", async () => {
    const api = stubApi();
    Object.assign(api.refuse, { next: 1, wait: 7 });
    const failed: unknown[] = [];
    const answers: unknown[] = [];
    const poller = createPoller<DocumentList>(
      () => ({
        load: () => listDocuments(),
        onResult: (result) => answers.push(result),
        onFailure: (caught) => failed.push(caught),
        again: () => false,
        signature: () => "",
      }),
      () => false,
    );
    poller.refresh();
    await pass(20_000);
    expect(failed).toEqual([]); // no "The list did not refresh"
    expect(answers).toHaveLength(1);
    const gets = api.sent.filter((request) => request.path === "/api/documents");
    expect(gets.map((request) => request.status)).toEqual([429, 200]);
    expect(gets[1]!.at - gets[0]!.at).toBeGreaterThanOrEqual(7000);
    poller.stop();
  });

  it("the tray asks one drop a round, the one asked longest ago, and passes a 429 to the poller", async () => {
    const asked = new Map<string, number>();
    const seen: string[] = [];
    const load = async (id: string) => {
      seen.push(id);
      if (id === "gone") throw new ApiError(404, "not_found", "Not found.");
      return { id, documents: [] } as unknown as BatchView;
    };
    const rounds = [];
    for (let i = 0; i < 4; i += 1) rounds.push(await askBatches(["a", "b", "gone"], asked, load));
    expect(seen).toEqual(["a", "b", "gone", "a"]);
    expect(rounds.every((round) => round.skipped)).toBe(true);
    expect(rounds[2]).toMatchObject({ gone: ["gone"], failed: false });
    expect((await askBatches(["a"], asked, load)).skipped).toBe(false);
    const limited = async () => {
      throw new ApiError(429, "rate_limited", "Too many requests.", undefined, 2);
    };
    await expect(askBatches(["a"], asked, limited)).rejects.toMatchObject({ status: 429, retryAfter: 2 });
  });
});
