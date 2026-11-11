// The folder upload's run (#484): plan, register in pages of 1,000, send 4 at a time, each file
// to its item; Continue sends only what is still waiting, matched by path; a file that changed
// or is missing is said, not sent; the counts and the line the status bar says.

import { createHash } from "node:crypto";
import { describe, expect, it, vi } from "vitest";
import { buildManifest, fromFileList, pathKey } from "@/components/documents/folderPick";
import type { Picked } from "@/components/documents/folderPick";
import {
  EMPTY_FOLDER_UPLOADS,
  attentionOf,
  folderLine,
  folderUploadsReducer,
  itemProblemText,
  readingLine,
  refusedReason,
  restoredUpload,
  sendWaiting,
  startFolderUpload,
  withNotSent,
  wrongFolderText,
} from "@/components/documents/folderUpload";
import type { FolderEvent, FolderUploadApi } from "@/components/documents/folderUpload";
import type { NotSentRow } from "@/components/documents/FolderUploadRow";
import { FALLBACK_UPLOAD_LIMITS } from "@/components/documents/logic";
import { ApiError } from "@/lib/api";
import type { FolderUploadItem, FolderUploadPlanBody, FolderUploadSummary, NewFolderItem } from "@/lib/types";

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
function fakeServer(options: { refuse?: (item: FolderUploadItem) => ApiError | null; known?: Set<string> } = {}) {
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
        const already = !each.refused && each.sha256 !== undefined && options.known?.has(each.sha256);
        items.push({
          id: `i${items.length}`,
          seq: items.length,
          path_key: each.path_key,
          name: each.name,
          folder_id: each.folder_id,
          size: each.size,
          state: each.refused ? "failed" : already ? "duplicate" : "waiting",
          failure_code: each.refused ?? null,
          document_id: already ? `d-${each.sha256}` : null,
          document: already ? { status: "ready", failure_code: null, folder_id: "f9", in_trash: false } : null,
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
    source_name: "A",
    target: "folder",
    parent_folder_id: null,
    merge: true,
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
    held: 0,
    held_message: null,
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

  it("registers only a kind Kaagaz can't take as refused; the server judges size and emptiness", async () => {
    const pick = fromFileList([picked("A/notes.odt"), picked("A/bill.pdf"), picked("A/zero.pdf", 0)]);
    if (!pick) throw new Error("empty");
    const server = fakeServer();
    await startFolderUpload(
      server.api,
      { id: "u2", userId: "me", source: { picked: pick, manifest: buildManifest(pick) }, target: { kind: "folder", parentId: null }, merge: true },
      () => undefined,
      never,
    );
    expect(server.registers[0]?.map((each) => each.refused)).toEqual(["unsupported_type", undefined, undefined]);
    expect(server.sentIds).toHaveLength(2);
    expect(refusedReason({ name: "big.pdf" })).toBeUndefined();
    expect(refusedReason({ name: "notes.odt" })).toBe("unsupported_type");
  });

  it("plans only the bytes it will send, and still registers a refused file with its size (#516)", async () => {
    const pick = fromFileList([picked("A/notes.odt", 100), picked("A/bill.pdf", 5)]);
    if (!pick) throw new Error("empty");
    const server = fakeServer();
    const plans: FolderUploadPlanBody[] = [];
    const plan = server.api.plan;
    server.api.plan = (body) => (plans.push(body), plan(body));
    await startFolderUpload(
      server.api,
      { id: "u2b", userId: "me", source: { picked: pick, manifest: buildManifest(pick) }, target: { kind: "folder", parentId: null }, merge: true },
      () => undefined,
      never,
    );
    expect(plans.map((body) => [body.file_count, body.total_bytes])).toEqual([[2, 5]]);
    expect(server.registers[0]?.map((each) => [each.size, each.refused])).toEqual([
      [100, "unsupported_type"],
      [5, undefined],
    ]);
  });

  it("waits the server's Retry-After while another plan of the organisation is built, then plans", async () => {
    const pick = pickOf(["A/a.pdf"]);
    const server = fakeServer();
    const plan = server.api.plan;
    let asked = 0;
    server.api.plan = async (body) => {
      asked += 1;
      if (asked < 3) throw new ApiError(429, "plan_busy", "Another folder is being planned.", undefined, 0.001);
      return plan(body);
    };
    const events: FolderEvent[] = [];
    await startFolderUpload(
      server.api,
      { id: "pb", userId: "me", source: { picked: pick, manifest: buildManifest(pick) }, target: { kind: "folder", parentId: null }, merge: true },
      run(events),
      never,
    );
    expect(asked).toBe(3);
    expect(events.at(-1)).toEqual({ type: "phase", id: "pb", phase: "done" });
  });

  it("is refused in the server's words while another folder upload of the organisation still sends", async () => {
    const pick = pickOf(["A/a.pdf"]);
    const server = fakeServer();
    const words = "Another folder upload is still sending in this organisation. Try again in a few minutes.";
    server.api.plan = async () => {
      throw new ApiError(409, "upload_in_progress", words);
    };
    const events: FolderEvent[] = [];
    await startFolderUpload(
      server.api,
      { id: "ip", userId: "me", source: { picked: pick, manifest: buildManifest(pick) }, target: { kind: "folder", parentId: null }, merge: true },
      run(events),
      never,
    );
    expect(events.at(-1)).toEqual({ type: "phase", id: "ip", phase: "refused", message: words });
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

  it("counts the files the reading pause holds apart from those reading, with the pause's own words (#478)", () => {
    const summary: FolderUploadSummary = {
      ...summaryOf("u", []),
      file_count: 5,
      registered: 5,
      stored: 5,
      reading: 0,
      ready: 2,
      held: 3,
      held_message: "Reading is paused. Your organisation has reached its daily page limit. This file will be read on its own when the limit resets, or sooner if it is raised.",
    };
    let state = folderUploadsReducer(start, { type: "counts", id: "u", summary });
    expect(state.u).toMatchObject({ reading: 0, held: 3, heldMessage: summary.held_message });
    expect(readingLine(state.u!)).toBe("Reading: 2 of 5 ready, 3 waiting. Reading can take a while for big folders.");
    state = folderUploadsReducer(state, { type: "counts", id: "u", summary: { ...summary, held: null, held_message: null } });
    expect(state.u).toMatchObject({ held: 3, heldMessage: summary.held_message }); // the list leaves them out
    state = folderUploadsReducer(state, { type: "counts", id: "u", summary: { ...summary, ready: 5, held: 0, held_message: null } });
    expect(state.u).toMatchObject({ held: 0, heldMessage: null });
    expect(readingLine(state.u!)).toBeNull();
  });

  it("names the folder to choose again after a reload, and gives a plain fix for a refused file", () => {
    const waiting = folderUploadsReducer(start, { type: "phase", id: "u", phase: "needs_pick" });
    expect(folderLine(waiting.u!)).toBe("Folder “A”: 0 of 5,000 uploaded, 5,000 not sent yet. Choose “A” again to send them.");
    const stopped = folderUploadsReducer(start, { type: "phase", id: "u", phase: "stopped" });
    expect(folderLine(stopped.u!)).toBe("Folder “A”: stopped at 0 of 5,000 uploaded, 5,000 not sent yet");
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
    // #549: a zip in a folder upload is kept as a file by design, never "Failed".
    const keptZip = { status: "needs_reader" as const, failure_code: null, folder_id: "f", in_trash: false };
    expect(itemProblemText(item({ name: "Scans.ZIP", state: "stored", document_id: "d", document: keptZip }), FALLBACK_UPLOAD_LIMITS)).toBe(
      "Kept as a file, not unpacked: Kaagaz unpacks a zip only when it is uploaded on its own.",
    );
    expect(itemProblemText(item({ name: "old.doc", state: "stored", document_id: "d", document: keptZip }), FALLBACK_UPLOAD_LIMITS)).not.toMatch(/^Kept/);
    expect(itemProblemText(item({ failure_code: "bad_name" }), FALLBACK_UPLOAD_LIMITS)).toBe(
      "Kaagaz can’t keep this file’s name: it has a hidden character, or another file in its folder has the same name. Rename it, then upload it again.",
    );
  });

  it("counts what needs attention once, as the server lists it: failed or not read, never the files not sent", () => {
    let state = folderUploadsReducer(start, { type: "phase", id: "u", phase: "sending" });
    state = folderUploadsReducer(state, { type: "sent", id: "u", itemId: "i0", outcome: "failed" });
    state = folderUploadsReducer(state, { type: "counts", id: "u", summary: { ...summaryOf("u", []), file_count: 5000, failed: 1, problem: 1 } });
    for (const itemId of ["i1", "i2"]) {
      state = folderUploadsReducer(state, { type: "notSent", id: "u", itemId, name: "x.pdf", message: "Lost.", retry: true, item: {} as never });
    }
    expect(attentionOf(state.u!)).toBe(2);
    expect(folderLine(state.u!)).toBe("Folder “A”: 0 of 5,000 uploaded, 2 need attention");
  });

  it("keeps the first 20 files not sent and counts the rest, copying nothing once 20 are kept", () => {
    let shown: { rows: NotSentRow[]; total: number } = { rows: [], total: 0 };
    let copies = 0;
    for (let n = 0; n < 50_000; n++) {
      const before = shown.rows;
      shown = withNotSent(shown, { item: { id: `i${n}` } as never, message: "Lost.", retry: true }, n + 1);
      if (shown.rows !== before) copies += 1;
    }
    expect(shown.total).toBe(50_000);
    expect(shown.rows.map((row) => row.item.id)).toEqual(Array.from({ length: 20 }, (_, n) => `i${n}`));
    expect(copies).toBe(20);
  });
});

describe("after a reload", () => {
  const summary = (over: Partial<FolderUploadSummary>): FolderUploadSummary => ({
    ...summaryOf("r", []),
    root_name: "A (2)",
    source_name: "A",
    file_count: 3,
    ...over,
  });

  it("brings back an upload with files waiting, or one whose listing stopped part way, by its own folder's name", () => {
    expect(restoredUpload(summary({ registered: 3, stored: 3 }))).toBeNull();
    const waiting = restoredUpload(summary({ registered: 3, waiting: 2, stored: 1 }));
    expect(waiting?.listed).toBe(true);
    const cut = restoredUpload(summary({ registered: 1, waiting: 0, stored: 1, parent_folder_id: "p9", merge: false }));
    expect(cut).toMatchObject({ listed: false, again: { target: { kind: "folder", parentId: "p9" }, merge: false } });
    const state = cut!.events.reduce(folderUploadsReducer, EMPTY_FOLDER_UPLOADS);
    expect(state.r).toMatchObject({ rootName: "A", phase: "needs_pick", stored: 1 });
    expect(folderLine(state.r!)).toBe("Folder “A”: 1 of 3 uploaded, 2 not sent yet. Choose “A” again to send them.");
    expect(restoredUpload(summary({ registered: 0, target: "department" }))?.again.target).toEqual({ kind: "department" });
  });

  it("re-picked, plans again with the same id and place, lists the rest and sends what waits", async () => {
    const pick = pickOf(["A/one.pdf", "A/B/two.pdf", "A/B/three.pdf"]);
    const server = fakeServer();
    const bodies: FolderUploadPlanBody[] = [];
    const plan = server.api.plan;
    server.api.plan = async (body) => {
      bodies.push(body);
      return plan(body);
    };
    // The first visit listed and sent one.pdf, then the page was reloaded.
    await server.api.register("r", "me", [{ path_key: await pathKey("A/one.pdf"), name: "one.pdf", folder_id: "f0", size: 10 }]);
    server.items[0]!.state = "stored";
    const { again } = restoredUpload(summary({ registered: 1, parent_folder_id: "p9", merge: false }))!;
    await startFolderUpload(server.api, { id: "r", userId: "me", source: { picked: pick, manifest: buildManifest(pick) }, ...again }, () => undefined, never);
    expect(bodies[0]).toMatchObject({ id: "r", target: "folder", parent_folder_id: "p9", merge: false });
    expect(server.items.map((item) => [item.name, item.state])).toEqual([
      ["one.pdf", "stored"],
      ["two.pdf", "stored"],
      ["three.pdf", "stored"],
    ]);
    expect(server.sentIds).toEqual(["i1", "i2"]);
  });

  it("says so when another folder is picked to continue", () => {
    expect(wrongFolderText("A", "A")).toBeNull();
    expect(wrongFolderText("A", "B")).toBe("This is “B”. Choose “A” to continue.");
  });
});

describe("adding new files only", () => {
  const sha = (text: string) => createHash("sha256").update(text).digest("hex");
  /** A picked file whose bytes are `text`. */
  function filled(path: string, text: string): File {
    const file = new File([text], path.split("/").at(-1) ?? path);
    Object.defineProperty(file, "webkitRelativePath", { value: path });
    return file;
  }
  const sourceOf = (files: File[]) => {
    const pick = fromFileList(files);
    if (!pick) throw new Error("empty");
    return { picked: pick, manifest: buildManifest(pick) };
  };
  const many = (count: number) => Array.from({ length: count }, (_, n) => filled(`A/f${n}.pdf`, `bill ${n}`));
  const target = { kind: "folder" as const, parentId: null };

  it("hashes one file at a time: the most reads in flight is 1", async () => {
    const read = Blob.prototype.arrayBuffer;
    let inFlight = 0;
    let most = 0;
    const spy = vi.spyOn(Blob.prototype, "arrayBuffer").mockImplementation(async function (this: Blob) {
      inFlight += 1;
      most = Math.max(most, inFlight);
      await new Promise((resolve) => setImmediate(resolve));
      try {
        return await read.call(this);
      } finally {
        inFlight -= 1;
      }
    });
    try {
      const server = fakeServer();
      await startFolderUpload(server.api, { id: "h1", userId: "me", source: sourceOf(many(120)), target, merge: true, onlyNew: true }, () => undefined, never);
      expect(spy).toHaveBeenCalledTimes(120);
      expect(most).toBe(1);
      expect(server.registers[0]?.[7]?.sha256).toBe(sha("bill 7"));
    } finally {
      spy.mockRestore();
    }
  });

  it("registers sha256 only for files Kaagaz could take, and never sends a file already in Kaagaz", async () => {
    const locked = filled("A/locked.pdf", "secret");
    Object.defineProperty(locked, "arrayBuffer", { value: () => Promise.reject(new Error("NotReadableError")) });
    const files = [
      filled("A/notes.odt", "words"), // a kind Kaagaz can't take
      filled("A/zero.pdf", ""), // empty
      filled("A/big.pdf", "x".repeat(60)), // over the PDF limit below
      locked, // the browser can't read it
      filled("A/bill.pdf", "bill"),
      filled("A/scan.png", "scan"),
    ];
    const limits = { ...FALLBACK_UPLOAD_LIMITS, pdf_bytes: 50 };
    const server = fakeServer({ known: new Set([sha("bill")]) });
    const bodies: FolderUploadPlanBody[] = [];
    const plan = server.api.plan;
    server.api.plan = async (body) => {
      bodies.push(body);
      return plan(body);
    };
    await startFolderUpload(server.api, { id: "h2", userId: "me", source: sourceOf(files), target, merge: true, limits, onlyNew: true }, () => undefined, never);
    expect(bodies[0]?.only_new).toBe(true);
    expect(server.registers[0]?.map((each) => [each.name, each.sha256])).toEqual([
      ["notes.odt", undefined],
      ["zero.pdf", undefined],
      ["big.pdf", undefined],
      ["locked.pdf", undefined],
      ["bill.pdf", sha("bill")],
      ["scan.png", sha("scan")],
    ]);
    // Those not hashed go as today; the server judges them.
    expect(server.items.find((item) => item.name === "bill.pdf")?.state).toBe("duplicate");
    expect(server.sentIds.map((itemId) => server.items.find((item) => item.id === itemId)?.name).sort()).toEqual([
      "big.pdf",
      "locked.pdf",
      "scan.png",
      "zero.pdf",
    ]);

    const plain = fakeServer();
    await startFolderUpload(plain.api, { id: "h3", userId: "me", source: sourceOf(files), target, merge: true, limits }, () => undefined, never);
    expect(plain.registers[0]?.every((each) => !("sha256" in each))).toBe(true);
  });

  it("says “checking your files: N of M” every 50 files, then how many were already in Kaagaz", async () => {
    const files = many(120);
    const server = fakeServer({ known: new Set(files.slice(0, 100).map((_, n) => sha(`bill ${n}`))) });
    const events: FolderEvent[] = [];
    await startFolderUpload(server.api, { id: "h4", userId: "me", source: sourceOf(files), target, merge: true, onlyNew: true }, run(events), never);
    const checked = events.flatMap((event) => (event.type === "checked" ? [event.done] : []));
    expect(checked).toEqual([0, 50, 100, 120]);
    const upTo = events.findIndex((event) => event.type === "checked" && event.done === 50);
    const checking = events.slice(0, upTo + 1).reduce(folderUploadsReducer, EMPTY_FOLDER_UPLOADS);
    expect(folderLine(checking.h4!)).toBe("Folder “A”: checking your files: 50 of 120");
    const done = events.reduce(folderUploadsReducer, EMPTY_FOLDER_UPLOADS);
    expect(folderLine(done.h4!)).toBe("Folder “A”: 120 of 120 uploaded, 100 already in Kaagaz");
    expect(server.sentIds).toHaveLength(20);
  });

  it("stops while checking, keeping what is registered, and Continue checks the rest", async () => {
    const source = sourceOf(many(2500));
    const server = fakeServer();
    const events: FolderEvent[] = [];
    const stop = () => events.some((event) => event.type === "checked" && event.done >= 1200);
    await startFolderUpload(server.api, { id: "h5", userId: "me", source, target, merge: true, onlyNew: true }, run(events), stop);
    expect(server.registers.map((page) => page.length)).toEqual([1000]);
    expect(server.items).toHaveLength(1000);
    expect(server.sentIds).toEqual([]);
    expect(events.at(-1)).toEqual({ type: "phase", id: "h5", phase: "stopped" });
    expect(events.filter((event) => event.type === "checked").at(-1)).toMatchObject({ done: 1200 });

    await startFolderUpload(server.api, { id: "h5", userId: "me", source, target, merge: true, onlyNew: true }, () => undefined, never);
    expect(server.items).toHaveLength(2500);
    expect(server.items.every((item) => item.state === "stored")).toBe(true);
  });

  it("keeps listing with “listing its files…” when not checking, and checks again after a reload when it was adding", () => {
    const start = folderUploadsReducer(EMPTY_FOLDER_UPLOADS, { type: "start", id: "u", rootName: "A", total: 5000 });
    const listing = folderUploadsReducer(start, { type: "phase", id: "u", phase: "registering" });
    expect(folderLine(listing.u!)).toBe("Folder “A”: listing its files…");
    const checking = folderUploadsReducer(listing, { type: "checked", id: "u", done: 1240 });
    expect(folderLine(checking.u!)).toBe("Folder “A”: checking your files: 1,240 of 5,000");
    // The status bar says it again only each tenth of the files checked.
    let state = listing;
    const said = new Set<string>([state.u!.spoken]);
    for (let done = 0; done <= 1240; done += 50) {
      state = folderUploadsReducer(state, { type: "checked", id: "u", done });
      said.add(state.u!.spoken);
    }
    expect([...said]).toEqual([
      "Folder “A”: listing its files…",
      "Folder “A”: checking your files: 0 of 5,000",
      "Folder “A”: checking your files: 500 of 5,000",
      "Folder “A”: checking your files: 1,000 of 5,000",
    ]);
    const cut = { ...summaryOf("r", []), file_count: 3, registered: 1 };
    expect(restoredUpload({ ...cut, merge: true })?.again.onlyNew).toBe(true);
    expect(restoredUpload({ ...cut, merge: false })?.again.onlyNew).toBe(false);
  });
});
