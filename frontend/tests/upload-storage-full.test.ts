// Storage limit per organisation (#516): an upload that would take the organisation past its
// storage limit is refused with `storage_full`, at its complete or at a folder upload's plan (the
// server no longer checks the resumable start, which can't tell a duplicate; one refused there is
// still handled the same). The row shows the API's message as it is, and it is never sent again by
// itself: not by the upload's own retries (whatever the status), not by Retry, not by Retry all.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildManifest, fromFileList } from "@/components/documents/folderPick";
import type { Picked } from "@/components/documents/folderPick";
import { buildTree } from "@/components/documents/folderTree";
import { EMPTY_FOLDER_UPLOADS, folderUploadsReducer, startFolderUpload } from "@/components/documents/folderUpload";
import type { FolderEvent, FolderUploadApi } from "@/components/documents/folderUpload";
import { FolderUploadRow } from "@/components/documents/FolderUploadRow";
import { canSendAgain, uploadErrorText } from "@/components/documents/logic";
import { BatchTray } from "@/components/documents/UploadArea";
import type { UploadEntry } from "@/components/documents/uploadQueue";
import { ApiError, isStorageFull, uploadDocument } from "@/lib/api";
import { settle } from "./fake-clock";

const FULL = "This organisation has used its 50 GB of storage. Empty the Trash or delete files, or ask Kaagaz to raise the limit.";
const DETAILS = { used_bytes: 50 * 1024 ** 3, limit_bytes: 50 * 1024 ** 3, limit_gb: 50 };
const full = (status: number) => new ApiError(status, "storage_full", FULL, DETAILS);
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

/**
 * A stand-in resumable upload API that refuses with storage_full (at `status`) at `where`:
 * the start (POST /documents/upload-sessions) or the complete. It counts every call.
 */
function refusingApi(where: "start" | "complete", status: number) {
  const calls: string[] = [];
  const refusal = { error: { code: "storage_full", message: FULL, details: DETAILS } };
  vi.stubGlobal("fetch", async (url: string) => {
    calls.push(url);
    if (url.endsWith("/candidates")) return json(200, { candidates: [] });
    if (url === "/api/documents/upload-sessions") {
      return where === "start" ? json(status, refusal) : json(200, { id: "s1", part_size: 8 * 1024 * 1024, part_count: 1 });
    }
    if (url.endsWith("/complete")) return json(status, refusal);
    throw new Error(`unexpected request ${url}`);
  });
  class FakeXhr {
    status = 204;
    responseText = "";
    upload = { onprogress: null };
    onload: (() => void) | null = null;
    onerror = null;
    onabort = null;
    withCredentials = false;
    open() {}
    setRequestHeader() {}
    getResponseHeader() {
      return null;
    }
    send() {
      queueMicrotask(() => this.onload?.());
    }
  }
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
  return calls;
}

/** Upload one file against the stand-in; what it was refused with, and whether it waited to go again. */
async function refusedWith(where: "start" | "complete", status: number) {
  const calls = refusingApi(where, status);
  const waits: boolean[] = [];
  let caught: unknown = null;
  let done = false;
  void uploadDocument(new File(["%PDF-1.7"], "deed.pdf"), "u1", () => {}, undefined, undefined, undefined, undefined, undefined, (waiting) =>
    waits.push(waiting),
  ).then(
    () => (done = true),
    (error: unknown) => {
      caught = error;
      done = true;
    },
  );
  await settle(() => done, 8_000);
  expect(done).toBe(true);
  return { caught, waits, calls };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("a single upload refused because storage is full", () => {
  // Whatever status the server gives it: a 507 (Insufficient Storage) would otherwise be sent again as a server error.
  for (const status of [413, 507]) {
    it(`is refused at the resumable start (${status}) with the API's words, asked once, never again`, async () => {
      const { caught, waits, calls } = await refusedWith("start", status);
      expect(isStorageFull(caught)).toBe(true);
      expect(caught).toMatchObject({ code: "storage_full", message: FULL, details: DETAILS });
      expect(calls.filter((url) => url === "/api/documents/upload-sessions")).toHaveLength(1);
      expect(waits).toEqual([]);
    });

    it(`is refused at the complete (${status}) and not completed again`, async () => {
      const { caught, waits, calls } = await refusedWith("complete", status);
      expect(isStorageFull(caught)).toBe(true);
      expect(calls.filter((url) => url.endsWith("/complete"))).toHaveLength(1);
      expect(waits).toEqual([]);
    });
  }

  it("shows the API's message as it is, and is not one to send again", () => {
    for (const status of [400, 409, 413, 429, 500, 503, 507]) {
      expect(uploadErrorText(full(status), true)).toBe(FULL);
      expect(canSendAgain(full(status))).toBe(false);
    }
    // Unchanged: a lost connection or a server error can be sent again; a refusal can't.
    expect(canSendAgain(new TypeError("Failed to fetch"))).toBe(true);
    expect(canSendAgain(new ApiError(503, "error", "Something went wrong."))).toBe(true);
    expect(canSendAgain(new ApiError(429, "rate_limited", "Too many."))).toBe(true);
    expect(canSendAgain(new ApiError(413, "too_large", "Too big."))).toBe(false);
    expect(isStorageFull(new ApiError(413, "too_large", "Too big."))).toBe(false);
  });

  it("has no Retry on its row, and is left out of Retry all not sent", () => {
    const row = (key: string, retry: boolean): UploadEntry => ({
      key,
      name: `${key}.pdf`,
      size: 1000,
      batchId: "6f1c2d3e-4a5b-4c6d-8e7f-901a2b3c4d5e",
      state: { kind: "error", message: retry ? "The server was restarting. Retry." : FULL, retry },
    });
    const tray = (entries: UploadEntry[]) =>
      renderToStaticMarkup(
        createElement(BatchTray, {
          entries,
          views: {},
          stored: [],
          onClearFinished: () => undefined,
          onRestore: () => undefined,
          onRetry: () => undefined,
          tree: buildTree([]),
          onUndone: () => undefined,
          canReread: true,
        }),
      );
    const html = tray([row("a", false), row("b", false)]);
    expect(html).toContain(FULL.replaceAll("'", "&#x27;"));
    expect(html).not.toContain("Retry");
    // One file not sent and one refused for storage: one to send again is not enough for Retry all.
    expect(tray([row("a", false), row("b", true)])).not.toContain("Retry all not sent");
  });
});

// --- folder uploads ------------------------------------------------------------------

function picked(path: string): File {
  const file = new File([new Uint8Array(10)], path.split("/").at(-1) ?? path);
  Object.defineProperty(file, "webkitRelativePath", { value: path });
  return file;
}

const pickOf = (paths: string[]): Picked => {
  const pick = fromFileList(paths.map(picked));
  if (!pick) throw new Error("empty");
  return pick;
};

/** A stand-in folder upload API: the plan refuses with `planRefusal`, or each file send with `sendRefusal`. */
function folderApi(planRefusal: ApiError | null, sendRefusal: ApiError | null) {
  let plans = 0;
  let sends = 0;
  const items: { id: string; seq: number; path_key: string; name: string; folder_id: string; size: number }[] = [];
  const api: FolderUploadApi = {
    plan: async (body) => {
      plans += 1;
      if (planRefusal) throw planRefusal;
      return { id: body.id, root_folder_id: "f0", root_name: "A", folder_ids: body.folders.map((_, n) => `f${n}`), created: 1, merged: 0 };
    },
    register: async (_id, _user, page) => {
      for (const each of page) items.push({ id: `i${items.length}`, seq: items.length, ...each });
    },
    items: async () => ({
      items: items.map((item) => ({ ...item, state: "waiting" as const, failure_code: null, document_id: null, document: null })),
      next_after: null,
    }),
    send: async () => {
      sends += 1;
      if (sendRefusal) throw sendRefusal;
      return { document: { id: "d" } as never, duplicate: false, in_trash: false };
    },
    summary: async (id) => ({
      id, root_folder_id: "f0", root_name: "A", source_name: "A", target: "folder", parent_folder_id: null, merge: true,
      created_at: "2026-11-05T10:00:00Z", file_count: items.length, total_bytes: 0, skipped_count: 0, registered: items.length,
      waiting: items.length, stored: 0, duplicate: 0, failed: 0, reading: 0, ready: 0, problem: 0, held: 0, held_message: null,
    }),
  };
  return { api, plans: () => plans, sends: () => sends };
}

async function runFolder(api: FolderUploadApi, paths: string[]): Promise<FolderEvent[]> {
  const pick = pickOf(paths);
  const events: FolderEvent[] = [];
  await startFolderUpload(
    api,
    { id: "u1", userId: "me", source: { picked: pick, manifest: buildManifest(pick) }, target: { kind: "folder", parentId: null }, merge: true },
    (event) => events.push(event),
    () => false,
  );
  return events;
}

describe("a folder upload refused because storage is full", () => {
  beforeEach(() => vi.useRealTimers());

  it("is refused at its plan with the API's words, asked once, and nothing is listed or sent", async () => {
    for (const status of [413, 507]) {
      const server = folderApi(full(status), null);
      const events = await runFolder(server.api, ["A/a.pdf", "A/b.pdf"]);
      expect(server.plans()).toBe(1);
      expect(server.sends()).toBe(0);
      expect(events.at(-1)).toEqual({ type: "phase", id: "u1", phase: "refused", message: FULL });
    }
  });

  it("says each file the server refused for storage as not sent, with no Retry", async () => {
    const server = folderApi(null, full(507));
    const events = await runFolder(server.api, ["A/a.pdf", "A/b.pdf"]);
    expect(server.sends()).toBe(2); // each once
    const notSent = events.flatMap((event) => (event.type === "notSent" ? [event] : []));
    expect(notSent.map((event) => [event.name, event.message, event.retry])).toEqual([
      ["a.pdf", FULL, false],
      ["b.pdf", FULL, false],
    ]);
    expect(events.some((event) => event.type === "sent")).toBe(false); // not counted as failed for good
  });

  it("shows the words on the folder's row, with no Retry and no Retry all failed", () => {
    let state = folderUploadsReducer(EMPTY_FOLDER_UPLOADS, { type: "start", id: "u", rootName: "A", total: 2 });
    state = folderUploadsReducer(state, { type: "phase", id: "u", phase: "done" });
    const item = (id: string) => ({ id, seq: 0, path_key: id, name: `${id}.pdf`, folder_id: "f0", size: 10, state: "waiting" as const, failure_code: null, document_id: null, document: null });
    const rowHtml = (phase: "done" | "refused") => {
      const view = phase === "done" ? state.u! : folderUploadsReducer(state, { type: "phase", id: "u", phase: "refused", message: FULL }).u!;
      return renderToStaticMarkup(
        createElement(FolderUploadRow, {
          view,
          held: true,
          notSent: phase === "done" ? { rows: [{ item: item("a"), message: FULL, retry: false }, { item: item("b"), message: FULL, retry: false }], total: 2 } : { rows: [], total: 0 },
          tree: buildTree([]),
          limits: { pdf_bytes: 1, image_bytes: 1, sheet_bytes: 1, max_pages: 1, readers: [] },
          actions: {
            stop: () => undefined,
            resume: () => undefined,
            chooseAgain: () => undefined,
            retry: () => undefined,
            retryAll: () => undefined,
            clear: () => undefined,
            loadItems: async () => ({ items: [], next_after: null }),
          },
        }),
      );
    };
    const files = rowHtml("done");
    expect(files.split(FULL.replaceAll("'", "&#x27;")).length - 1).toBe(2);
    expect(files).not.toContain("Retry");
    const folder = rowHtml("refused");
    expect(folder).toContain("Folder “A” was not uploaded.");
    expect(folder).toContain(FULL.replaceAll("'", "&#x27;"));
    expect(folder).not.toContain("Retry");
  });
});
