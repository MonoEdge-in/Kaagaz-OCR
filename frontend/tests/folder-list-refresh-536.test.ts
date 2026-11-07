// #536: a new folder shows in the folder lists as soon as it is on the server, not after a
// refresh. Folders appear in two ways during uploads: a folder upload makes its folders when it
// is planned (its start), and Kaagaz files a read file into a folder it may make just then
// (feature 28, after the file is read; storing a file never makes a folder). The shell's
// uploads (components/shell/Uploads.tsx) count both in `foldersChanged`, and each folder list
// shown during uploads asks again when it goes up. Rendered in node with the fake DOM.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, createElement, useEffect } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fire, install } from "./fake-dom";
import type { FakeDocument, FakeElement } from "./fake-dom";
import { EMPTY_QUEUE, queueReducer } from "@/components/documents/uploadQueue";
import type { UploadEntry } from "@/components/documents/uploadQueue";
import type { FolderTarget } from "@/components/documents/folderUpload";
import type { BatchView, DocumentSummary, FolderUploadSummary } from "@/lib/types";

const api = vi.hoisted(() => ({
  listFolders: vi.fn(),
  getBatch: vi.fn(),
  uploadDocument: vi.fn(),
  planFolderUpload: vi.fn(),
  registerFolderItems: vi.fn(),
  listFolderItems: vi.fn(),
  uploadFolderItem: vi.fn(),
  getFolderUpload: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...api,
  getAiSettings: () => new Promise(() => undefined), // unknown: nothing capped
  getUploadLimits: () => new Promise(() => undefined), // the platform's limits apply
  listFolderUploads: async () => ({ items: [] }), // nothing to bring back
  getPreviousFolderUpload: async () => ({ previous: null }),
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ me: { user: { id: "u1", role: "admin" } } }) }));
vi.mock("@/components/documents/pageCount", () => ({ checkPages: async () => ({ pages: null, problem: null }) }));
// The tray and the dialogs are not under test: the dialog starts the upload as soon as it shows.
vi.mock("@/components/documents/UploadArea", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/documents/UploadArea")>()),
  UploadStatus: () => null,
}));
vi.mock("@/components/documents/FolderUploadDialog", () => ({
  FolderReadingDialog: () => null,
  FolderUploadDialog: function FolderUploadDialog({
    onStart,
  }: {
    onStart(target: FolderTarget, merge: boolean, onlyNew: boolean): void;
  }) {
    useEffect(() => onStart({ kind: "folder", parentId: null }, false, false), [onStart]);
    return null;
  },
}));

const document = (id: string, more: Partial<DocumentSummary> = {}): DocumentSummary => ({
  id,
  display_name: `${id}.pdf`,
  detected_type: "pdf",
  size_bytes: 3,
  status: "clean",
  failure_code: null,
  status_message: "Reading page 1 of 1…",
  page_count: 1,
  created_at: "2026-11-07T10:00:00Z",
  uploaded_by_name: null,
  folder_id: null,
  tags: [],
  deleted_at: null,
  version_no: 1,
  family_id: id,
  can_delete: true,
  attempts: 0,
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
const ready = (id: string, more: Partial<DocumentSummary> = {}) =>
  document(id, { status: "ready", status_message: "Ready.", ...more });
const filed = (id: string) => ready(id, { autofile: "filed", folder_id: "invoices" });

const batchOf = (documents: DocumentSummary[]): BatchView =>
  ({ id: "b", documents, counts: {}, pages_read: 0, pages_total: 0, time_left: null }) as unknown as BatchView;

describe("the upload queue counts each file Kaagaz files (feature 28)", () => {
  const reading = (id: string): UploadEntry => ({
    key: id,
    name: `${id}.pdf`,
    size: 3,
    batchId: "b",
    state: { kind: "reading", documentId: id, document: document(id) },
  });
  const start = () => queueReducer(EMPTY_QUEUE, { type: "add", entries: [reading("d1"), reading("d2")] });

  it("counts a file filed once read, before its batch settles", () => {
    const queue = queueReducer(start(), { type: "batch", batchId: "b", documents: [filed("d1"), document("d2")], add: false });
    expect([queue.filed, queue.settled]).toEqual([1, 0]);
  });

  it("does not count a file still being read, one read but not filed, or the same answer again", () => {
    let queue = queueReducer(start(), { type: "batch", batchId: "b", documents: [document("d1"), ready("d2")], add: false });
    expect(queue.filed).toBe(0);
    queue = queueReducer(queue, { type: "batch", batchId: "b", documents: [filed("d1"), ready("d2")], add: false });
    queue = queueReducer(queue, { type: "batch", batchId: "b", documents: [filed("d1"), ready("d2")], add: false });
    expect(queue.filed).toBe(1);
  });
});

type Shared = ReturnType<typeof import("@/components/shell/Uploads").useSharedUploads>;

let doc: FakeDocument;
let root: Root | null = null;
let shared: Shared | null = null;

async function mountShell() {
  const { UploadsProvider, useSharedUploads } = await import("@/components/shell/Uploads");
  function Probe() {
    shared = useSharedUploads(() => undefined);
    return null;
  }
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  await act(async () => root!.render(createElement(UploadsProvider, null, createElement(Probe))));
  return container;
}

/** Let the fakes' promises and the effects they cause run. */
const flush = () => act(async () => void (await vi.advanceTimersByTimeAsync(0)));

/** Pick the folder "Tax" holding a.pdf with the shell's folder input. */
async function pickFolder(container: FakeElement) {
  const input = container.all().find((element) => element.getAttribute("id") === "folder-input-shell")!;
  const file = new File(["abc"], "a.pdf", { type: "application/pdf" });
  Object.defineProperty(file, "webkitRelativePath", { value: "Tax/a.pdf" });
  (input as unknown as { files: File[] }).files = [file];
  await act(async () => void fire(input, "change"));
  await flush();
}

const SUMMARY = {
  id: "fu",
  root_folder_id: "tax",
  root_name: "Tax",
  source_name: "Tax",
  target: "folder",
  parent_folder_id: null,
  merge: false,
  created_at: "2026-11-07T10:00:00Z",
  file_count: 1,
  total_bytes: 3,
  skipped_count: 0,
  registered: 1,
  waiting: 0,
  stored: 1,
  duplicate: 0,
  failed: 0,
  reading: 0,
  ready: 1,
  problem: 0,
  held: 0,
  held_message: null,
} satisfies FolderUploadSummary;

describe("the shell's folder list asks again as soon as new folders exist (#536)", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    doc = install();
    vi.stubGlobal("navigator", { onLine: true, userAgent: "node" });
    api.listFolders.mockResolvedValue({ items: [] });
    api.planFolderUpload.mockResolvedValue({
      id: "fu",
      root_folder_id: "tax",
      root_name: "Tax",
      folder_ids: ["tax"],
      created: 1,
      merged: 0,
    });
  });
  afterEach(async () => {
    await act(async () => root?.unmount());
    root = null;
    shared = null;
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("asks once a folder upload is planned, while its files are still being listed", async () => {
    api.registerFolderItems.mockReturnValue(new Promise(() => undefined)); // still listing
    const container = await mountShell();
    expect(api.listFolders).toHaveBeenCalledTimes(1);
    const before = shared!.foldersChanged;
    await pickFolder(container);
    expect(api.planFolderUpload).toHaveBeenCalledTimes(1);
    expect(api.listFolders).toHaveBeenCalledTimes(2);
    expect(shared!.foldersChanged).toBeGreaterThan(before);
  });

  it("asks again when the folder upload finishes", async () => {
    const { pathKey } = await import("@/components/documents/folderPick");
    const item = { id: "i1", seq: 1, path_key: await pathKey("Tax/a.pdf"), name: "a.pdf", folder_id: "tax", size: 3 };
    api.registerFolderItems.mockResolvedValue(undefined);
    api.listFolderItems.mockResolvedValue({ items: [item], next_after: null });
    let sent: (reply: { duplicate: boolean }) => void = () => undefined;
    api.uploadFolderItem.mockReturnValue(new Promise((resolve) => (sent = resolve)));
    api.getFolderUpload.mockResolvedValue(SUMMARY);
    const container = await mountShell();
    await pickFolder(container);
    expect(api.uploadFolderItem).toHaveBeenCalledTimes(1);
    expect(api.listFolders).toHaveBeenCalledTimes(2); // the first load, then planned
    sent({ duplicate: false });
    await flush();
    expect(api.listFolders).toHaveBeenCalledTimes(3); // and done
  });

  it("asks when a file is filed before its batch settles, and not again while nothing changes", async () => {
    api.uploadDocument
      .mockResolvedValueOnce({ document: document("d1"), duplicate: false, in_trash: false })
      .mockResolvedValueOnce({ document: document("d2"), duplicate: false, in_trash: false });
    api.getBatch.mockResolvedValue(batchOf([filed("d1"), document("d2")]));
    await mountShell();
    await act(() => shared!.uploads.addFiles([new File(["abc"], "d1.pdf"), new File(["abc"], "d2.pdf")]));
    await flush();
    expect(api.getBatch).toHaveBeenCalled();
    expect(shared!.uploads.settled).toBe(0); // d2 is still being read
    expect(api.listFolders).toHaveBeenCalledTimes(2);
    const rounds = api.getBatch.mock.calls.length;
    await act(async () => void (await vi.advanceTimersByTimeAsync(20_000))); // more rounds, the same answer
    expect(api.getBatch.mock.calls.length).toBeGreaterThan(rounds);
    expect(api.listFolders).toHaveBeenCalledTimes(2);
  });

  it("does not ask while nothing changes", async () => {
    await mountShell();
    await act(async () => void (await vi.advanceTimersByTimeAsync(60_000)));
    expect(api.listFolders).toHaveBeenCalledTimes(1);
  });
});

describe("the documents screen's folder tree asks again with the shell's count (#536)", () => {
  it("reloads its folders when foldersChanged goes up", () => {
    const source = readFileSync(join(__dirname, "../components/documents/DocumentsView.tsx"), "utf8");
    const effect = /listFolders\(\)\.then\([\s\S]*?\}, \[([^\]]*)\]\);/.exec(source);
    expect(effect?.[1]).toContain("foldersChanged");
  });
});
