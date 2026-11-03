// The folder upload's run (#484): plan, register in pages of 1,000, send 4 at a time, each file
// to its item; Continue sends only what is still waiting, matched by path; a file that changed
// or is missing is said, not sent; the counts and the line the status bar says.

import { describe, expect, it } from "vitest";
import { buildManifest, fromFileList } from "@/components/documents/folderPick";
import type { Picked } from "@/components/documents/folderPick";
import {
  EMPTY_FOLDER_UPLOADS,
  folderLine,
  folderUploadsReducer,
  itemProblemText,
  refusedReason,
  sendWaiting,
  startFolderUpload,
} from "@/components/documents/folderUpload";
import type { FolderEvent, FolderUploadApi } from "@/components/documents/folderUpload";
import { FALLBACK_UPLOAD_LIMITS } from "@/components/documents/logic";
import { ApiError } from "@/lib/api";
import type { FolderUploadItem, FolderUploadSummary, NewFolderItem } from "@/lib/types";

function picked(path: string, size = 10): File {
  const file = new File([new Uint8Array(size)], path.split("/").at(-1) ?? path);
  Object.defineProperty(file, "webkitRelativePath", { value: path });
  return file;
}

const pickOf = (paths: string[]): Picked => {
  const pick = fromFileList(paths.map((path) => picked(path)));
  if (!pick) throw new Error("empty");
  return pick;
};

/** An in-memory server: the plan gives folder ids f0, f1…; items keep their folder and state. */
function fakeServer(options: { refuse?: (item: FolderUploadItem) => ApiError | null } = {}) {
  const items: FolderUploadItem[] = [];
  const registers: NewFolderItem[][] = [];
  let inFlight = 0;
  let mostInFlight = 0;
  const sentIds: string[] = [];
  const api: FolderUploadApi = {
    plan: async (body) => ({
      id: body.id,
      root_folder_id: "f0",
      root_name: body.folders[0]!.name,
      folder_ids: body.folders.map((_, index) => `f${index}`),
      created: body.folders.length,
      merged: 0,
    }),
    register: async (_id, _user, page) => {
      registers.push(page);
      for (const each of page) {
        if (items.some((item) => item.path_key === each.path_key)) continue;
        items.push({
          id: `i${items.length}`,
          seq: items.length,
          path_key: each.path_key,
          name: each.name,
          folder_id: each.folder_id,
          size: each.size,
          state: each.refused ? "failed" : "waiting",
          failure_code: each.refused ?? null,
          document_id: null,
          document: null,
        });
      }
    },
    items: async (_id, view, after, limit) => {
      const shown = items.filter((item) => item.seq > after && (view !== "waiting" || item.state === "waiting"));
      const page = shown.slice(0, limit);
      return { items: page, next_after: shown.length > limit ? (page.at(-1)?.seq ?? null) : null };
    },
    send: async (_file, item) => {
      inFlight += 1;
      mostInFlight = Math.max(mostInFlight, inFlight);
      sentIds.push(item.id);
      await new Promise((resolve) => setImmediate(resolve));
      inFlight -= 1;
      const refusal = options.refuse?.(item);
      if (refusal) throw refusal;
      item.state = "stored";
      return { document: { id: `d-${item.id}` } as never, duplicate: false, in_trash: false };
    },
    summary: async (id) => summaryOf(id, items),
  };
  return { api, items, registers, sentIds, mostInFlight: () => mostInFlight };
}

function summaryOf(id: string, items: FolderUploadItem[]): FolderUploadSummary {
  const count = (state: FolderUploadItem["state"]) => items.filter((item) => item.state === state).length;
  return {
    id,
    root_folder_id: "f0",
    root_name: "A",
    target: "folder",
    created_at: "2026-11-03T10:00:00Z",
    file_count: items.length,
    total_bytes: 0,
    skipped_count: 0,
    registered: items.length,
    waiting: count("waiting"),
    stored: count("stored"),
    duplicate: count("duplicate"),
    failed: count("failed"),
    reading: 0,
    ready: count("stored"),
    problem: 0,
  };
}

const run = (events: FolderEvent[]) => (event: FolderEvent) => events.push(event);
const never = () => false;

describe("a folder upload's run", () => {
  it("plans, registers in pages of 1,000, and sends 4 at a time, each to its own folder", async () => {
    const paths = Array.from({ length: 2500 }, (_, n) => `A/B${n % 3}/f${n}.pdf`);
    const pick = pickOf(paths);
    const server = fakeServer();
    const events: FolderEvent[] = [];
    await startFolderUpload(
      server.api,
      { id: "u1", userId: "me", source: { picked: pick, manifest: buildManifest(pick) }, target: { kind: "folder", parentId: null }, merge: true },
      run(events),
      never,
    );
    expect(server.registers.map((page) => page.length)).toEqual([1000, 1000, 500]);
    expect(server.mostInFlight()).toBe(4);
    expect(server.items.every((item) => item.state === "stored")).toBe(true);
    // A/B1/f1.pdf went to the folder planned for "A/B1".
    const manifest = buildManifest(pick);
    const b1 = manifest.folders.findIndex((folder) => folder.name === "B1");
    expect(server.items.find((item) => item.name === "f1.pdf")?.folder_id).toBe(`f${b1}`);
    expect(events.at(-1)).toEqual({ type: "phase", id: "u1", phase: "done" });
  });

  it("registers a file Kaagaz can't take as refused, with its reason, and never sends it", async () => {
    const pick = pickOf(["A/notes.docx", "A/bill.pdf"]);
    const server = fakeServer();
    await startFolderUpload(
      server.api,
      { id: "u2", userId: "me", source: { picked: pick, manifest: buildManifest(pick) }, target: { kind: "folder", parentId: null }, merge: true },
      () => undefined,
      never,
    );
    expect(server.registers[0]?.map((each) => each.refused)).toEqual(["unsupported_type", undefined]);
    expect(server.sentIds).toHaveLength(1);
    expect(refusedReason({ name: "big.pdf", size: 60 * 1024 * 1024 }, FALLBACK_UPLOAD_LIMITS)).toBe("too_large");
    expect(refusedReason({ name: "zero.pdf", size: 0 }, FALLBACK_UPLOAD_LIMITS)).toBe("empty_file");
  });

  it("stops planning with the server's words when the plan is refused", async () => {
    const pick = pickOf(["A/a.pdf"]);
    const server = fakeServer();
    server.api.plan = async () => {
      throw new ApiError(409, "too_many_files", "A folder upload can hold at most 50,000 files.");
    };
    const events: FolderEvent[] = [];
    await startFolderUpload(
      server.api,
      { id: "u3", userId: "me", source: { picked: pick, manifest: buildManifest(pick) }, target: { kind: "folder", parentId: null }, merge: true },
      run(events),
      never,
    );
    expect(events.at(-1)).toEqual({
      type: "phase",
      id: "u3",
      phase: "refused",
      message: "A folder upload can hold at most 50,000 files.",
    });
    expect(server.sentIds).toEqual([]);
  });

  it("continue sends only the waiting items, matched by path, and says which file changed", async () => {
    const pick = pickOf(["A/one.pdf", "A/two.pdf", "A/three.pdf"]);
    const server = fakeServer();
    const source = { picked: pick, manifest: buildManifest(pick) };
    await startFolderUpload(server.api, { id: "u4", userId: "me", source, target: { kind: "folder", parentId: null }, merge: true }, () => undefined, () => server.sentIds.length >= 1);
    expect(server.items.map((item) => item.state)).toEqual(["stored", "waiting", "waiting"]);
    // After a reload: the same folder again, but "three.pdf" is now bigger and "two.pdf" is gone.
    const again = fromFileList([picked("A/one.pdf"), picked("A/three.pdf", 99)]);
    if (!again) throw new Error("empty");
    server.sentIds.length = 0;
    const events: FolderEvent[] = [];
    await sendWaiting(server.api, { id: "u4", userId: "me", source: { picked: again, manifest: buildManifest(again) } }, run(events), never);
    expect(server.sentIds).toEqual([]);
    const notSent = events.flatMap((event) => (event.type === "notSent" ? [event] : []));
    expect(notSent.map((event) => [event.name, event.message, event.retry])).toEqual([
      ["two.pdf", "This file isn’t in the folder you chose. Choose “A” again with all its files.", false],
      ["three.pdf", "This file changed after you chose the folder. Choose “A” again to send it as it is now.", false],
    ]);
  });

  it("sends as many at once as the server takes (files_at_once), never more than 4", async () => {
    const paths = Array.from({ length: 20 }, (_, n) => `A/f${n}.pdf`);
    const folderLimits = { max_files: 50000, max_bytes: 5 * 1024 ** 3, max_folders: 10000, max_depth: 100, calls_per_minute: 600 };
    for (const [filesAtOnce, most] of [[2, 2], [9, 4]] as const) {
      const pick = pickOf(paths);
      const server = fakeServer();
      const limits = { ...FALLBACK_UPLOAD_LIMITS, folder_upload: { ...folderLimits, files_at_once: filesAtOnce } };
      await startFolderUpload(
        server.api,
        { id: `n${filesAtOnce}`, userId: "me", source: { picked: pick, manifest: buildManifest(pick) }, target: { kind: "folder", parentId: null }, merge: true, limits },
        () => undefined,
        never,
      );
      expect(server.mostInFlight()).toBe(most);
    }
  });

  it("leaves a file whose size the server says changed waiting, to send with Continue upload", async () => {
    const pick = pickOf(["A/a.pdf"]);
    const server = fakeServer({ refuse: () => new ApiError(409, "size_changed", "This file changed after you chose the folder.") });
    const events: FolderEvent[] = [];
    await startFolderUpload(
      server.api,
      { id: "u7", userId: "me", source: { picked: pick, manifest: buildManifest(pick) }, target: { kind: "folder", parentId: null }, merge: true },
      run(events),
      never,
    );
    expect(events.some((event) => event.type === "sent")).toBe(false);
    expect(events.find((event) => event.type === "notSent")).toMatchObject({ name: "a.pdf", retry: false });
  });

  it("stops every send once another account has signed in, in its words", async () => {
    const pick = pickOf(["A/a.pdf", "A/b.pdf", "A/c.pdf", "A/d.pdf", "A/e.pdf", "A/f.pdf"]);
    const server = fakeServer({
      refuse: () => new ApiError(409, "session_changed", "You signed in as someone else in another tab."),
    });
    const events: FolderEvent[] = [];
    await startFolderUpload(
      server.api,
      { id: "u5", userId: "me", source: { picked: pick, manifest: buildManifest(pick) }, target: { kind: "folder", parentId: null }, merge: true },
      run(events),
      never,
    );
    expect(server.sentIds.length).toBeLessThanOrEqual(4); // the first round only
    expect(events.at(-1)).toMatchObject({ type: "phase", phase: "stopped", message: "You signed in as someone else in another tab." });
  });

  it("counts a refusal the server recorded as failed, and a lost send as not sent with Retry", async () => {
    const pick = pickOf(["A/a.pdf", "A/b.pdf"]);
    const server = fakeServer({
      refuse: (item) =>
        item.name === "a.pdf"
          ? new ApiError(409, "folder_gone", "The folder this file was meant for was deleted after the upload started.")
          : new ApiError(500, "error", "Something went wrong."),
    });
    const events: FolderEvent[] = [];
    await startFolderUpload(
      server.api,
      { id: "u6", userId: "me", source: { picked: pick, manifest: buildManifest(pick) }, target: { kind: "folder", parentId: null }, merge: true },
      run(events),
      never,
    );
    expect(events.filter((event) => event.type === "sent").map((event) => event.type === "sent" && event.outcome)).toEqual(["failed"]);
    expect(events.find((event) => event.type === "notSent")).toMatchObject({ name: "b.pdf", retry: true });
  });
});

describe("what the tray and the status bar say", () => {
  const start = folderUploadsReducer(EMPTY_FOLDER_UPLOADS, { type: "start", id: "u", rootName: "A", total: 5000 });

  it("keeps at most 4 sending rows, and counts every file without keeping its row", () => {
    let state = folderUploadsReducer(start, { type: "phase", id: "u", phase: "sending" });
    for (let n = 0; n < 5000; n++) {
      state = folderUploadsReducer(state, { type: "sending", id: "u", itemId: `i${n}`, name: `f${n}.pdf`, fraction: 0 });
      state = folderUploadsReducer(state, { type: "sent", id: "u", itemId: `i${n}`, outcome: n < 3 ? "failed" : "stored" });
      expect(state.u!.sending.length).toBeLessThanOrEqual(4);
    }
    expect(state.u!.stored).toBe(4997);
    expect(folderLine(state.u!)).toBe("Folder “A”: 4,997 of 5,000 uploaded, 3 need attention");
  });

  it("says the line again only on a new phase or each tenth of the files", () => {
    let state = folderUploadsReducer(start, { type: "phase", id: "u", phase: "sending" });
    const said = new Set<string>([state.u!.spoken]);
    for (let n = 0; n < 1240; n++) {
      state = folderUploadsReducer(state, { type: "sent", id: "u", itemId: `i${n}`, outcome: "stored" });
      said.add(state.u!.spoken);
    }
    expect([...said]).toEqual([
      "Uploading folder “A”: 0 of 5,000 uploaded",
      "Uploading folder “A”: 500 of 5,000 uploaded",
      "Uploading folder “A”: 1,000 of 5,000 uploaded",
    ]);
  });

  it("takes the server's counts, keeping the reading counts the list leaves out and the folder's own name", () => {
    const summary: FolderUploadSummary = {
      ...summaryOf("u", []),
      root_name: "A (2)",
      file_count: 5000,
      registered: 5000,
      waiting: 10,
      stored: 4990,
      reading: 7,
      ready: 4983,
      problem: 0,
    };
    let state = folderUploadsReducer(start, { type: "counts", id: "u", summary });
    state = folderUploadsReducer(state, { type: "counts", id: "u", summary: { ...summary, reading: null, ready: null, problem: null } });
    expect(state.u).toMatchObject({ rootName: "A", stored: 4990, reading: 7, ready: 4983, problem: 0 });
  });

  it("names the folder to choose again after a reload, and gives a plain fix for a refused file", () => {
    const waiting = folderUploadsReducer(start, { type: "phase", id: "u", phase: "needs_pick" });
    expect(folderLine(waiting.u!)).toBe("Folder “A”: 0 of 5,000 uploaded. Choose “A” again to send the rest.");
    const item = (over: Partial<FolderUploadItem>): FolderUploadItem => ({
      id: "i",
      seq: 0,
      path_key: "k",
      name: "big.pdf",
      folder_id: "f",
      size: 1,
      state: "failed",
      failure_code: null,
      document_id: null,
      document: null,
      ...over,
    });
    expect(itemProblemText(item({ failure_code: "too_large" }), FALLBACK_UPLOAD_LIMITS)).toBe("Too big: the most is 50 MB.");
    expect(itemProblemText(item({ name: "x.png", failure_code: "too_large" }), FALLBACK_UPLOAD_LIMITS)).toBe("Too big: the most is 20 MB.");
    expect(
      itemProblemText(
        item({
          state: "stored",
          document_id: "d",
          document: { status: "rejected", failure_code: "virus_found", folder_id: "f", in_trash: false },
        }),
        FALLBACK_UPLOAD_LIMITS,
      ),
    ).toBe("Virus found. Upload a clean copy of the file.");
    const failed = item({ state: "stored", document_id: "d", document: { status: "failed", failure_code: null, folder_id: "f", in_trash: false } });
    expect(itemProblemText(failed, FALLBACK_UPLOAD_LIMITS)).toBe("Failed. Read it again, or try another copy of it.");
  });
});
