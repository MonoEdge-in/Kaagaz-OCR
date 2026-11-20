// #699: a file still waiting in the Uploads tray can be cancelled (its ✕ Cancel button, named for the
// file); with two or more waiting in a drop, "Cancel all waiting" at the drop's top. A cancelled file
// leaves the queue and is never sent; a file sending, being read or finished has no Cancel. The drop's
// line and the drop remembered in this browser (read back after a reload) count only what is left.
// The queue in node (queueReducer); the upload loop through useUploads with React's own act() and a
// fake API, as tests/upload-remove-skip.test.ts; the tray rendered, and pressed in the fake DOM.

import { act, createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { STORE_KEY, readStored, summaryText, trayCounts } from "@/components/documents/batchTray";
import { buildTree } from "@/components/documents/folderTree";
import type { UploadState } from "@/components/documents/logic";
import { EMPTY_QUEUE, queueReducer } from "@/components/documents/uploadQueue";
import type { UploadEntry } from "@/components/documents/uploadQueue";
import type { DocumentSummary } from "@/lib/types";

const api = vi.hoisted(() => ({ uploadDocument: vi.fn(), counted: [] as string[], gate: null as Promise<void> | null }));

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  getAiSettings: () => new Promise(() => undefined),
  getBatch: () => new Promise(() => undefined),
  getUploadLimits: () => new Promise(() => undefined),
  isStorageFull: () => false,
  restoreDocument: vi.fn(),
  undoAutofile: vi.fn(),
  uploadDocument: api.uploadDocument,
  uploadVersion: vi.fn(),
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ me: { user: { id: "u1" } } }) }));
// A file's pages are counted just before its turn (it stays Waiting meanwhile); `gate` holds the count.
vi.mock("@/components/documents/pageCount", () => ({
  checkPages: async (file: File) => {
    api.counted.push(file.name);
    if (api.gate) await api.gate;
    return { pages: null, problem: null };
  },
}));

const BATCH = "6f1c2d3e-4a5b-4c6d-8e7f-901a2b3c4d5e";
const waiting: UploadState = { kind: "waiting" };
const sending: UploadState = { kind: "sending", fraction: 0.4 };
const failed: UploadState = { kind: "error", message: "This file is too large." };
const ready = { id: "d1", display_name: "a.pdf", status: "ready" } as DocumentSummary;
const reading: UploadState = { kind: "reading", documentId: "d2", document: { ...ready, id: "d2", status: "clean" } };
const done: UploadState = { kind: "done", documentId: "d1" };
const entry = (key: string, state: UploadState): UploadEntry => ({ key, name: `${key}.pdf`, size: 10, batchId: BATCH, state });
const keys = (entries: readonly UploadEntry[]) => entries.map((each) => each.key);

describe("the queue's cancel (#699)", () => {
  const start = () =>
    queueReducer(EMPTY_QUEUE, {
      type: "add",
      entries: [entry("w1", waiting), entry("s", sending), entry("w2", waiting), entry("r", reading), entry("d", done), entry("e", failed)],
    });

  it("takes out a waiting file only; a file sending, being read, finished or failed stays", () => {
    const queue = queueReducer(start(), { type: "cancel", keys: ["w1"] });
    expect(keys(queue.entries)).toEqual(["s", "w2", "r", "d", "e"]);
    for (const key of ["s", "r", "d", "e"]) {
      expect(queueReducer(start(), { type: "cancel", keys: [key] }).entries).toEqual(start().entries);
    }
  });

  it("Cancel all waiting takes out every waiting file of the drop and nothing else", () => {
    const queue = queueReducer(start(), { type: "cancel", keys: ["w1", "w2"] });
    expect(keys(queue.entries)).toEqual(["s", "r", "d", "e"]);
    expect(queue.batch).toEqual(["s", "r", "d", "e"]); // a cancelled file is no part of the drop
  });

  it("counts the drop without the cancelled files, and sums it up once nothing else is going", () => {
    let queue = queueReducer(EMPTY_QUEUE, { type: "add", entries: [entry("a", waiting), entry("b", waiting), entry("c", waiting)] });
    queue = queueReducer(queue, { type: "change", key: "a", change: { state: done } });
    expect(summaryText(trayCounts(queue.entries))).toBe("Batch of 3 files · 1 ready · 2 sending");
    queue = queueReducer(queue, { type: "cancel", keys: ["b"] });
    expect(summaryText(trayCounts(queue.entries))).toBe("Batch of 2 files · 1 ready · 1 sending");
    expect(queue.finished).toBeNull(); // c still waits
    queue = queueReducer(queue, { type: "cancel", keys: ["c"] });
    expect(queue.finished).toEqual({ uploaded: 1, already: 0, failed: 0 });
    expect(queue.settled).toBe(1);
  });
});

// --- the upload loop -------------------------------------------------------------------

type Uploads = ReturnType<typeof import("@/components/documents/UploadArea").useUploads>;

const target = () => ({ addEventListener: () => undefined, removeEventListener: () => undefined });

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

let store: MemoryStore;

async function mount() {
  const { useUploads } = await import("@/components/documents/UploadArea");
  const { createRoot } = await import("react-dom/client");
  let latest: Uploads | null = null;
  function Probe() {
    latest = useUploads(() => undefined);
    return null;
  }
  const container = { nodeType: 1, tagName: "DIV", namespaceURI: null, textContent: "", ownerDocument: null, ...target() };
  const root = createRoot(container as unknown as Element);
  await act(async () => root.render(createElement(Probe)));
  return {
    current: () => {
      if (latest === null) throw new Error("not rendered");
      return latest;
    },
    unmount: () => act(() => root.unmount()),
  };
}

describe("the upload loop after a cancel (#699)", () => {
  let replies: ((value: unknown) => void)[] = [];
  beforeEach(() => {
    replies = [];
    api.counted = [];
    api.gate = null;
    store = new MemoryStore();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const page = { visibilityState: "visible", activeElement: null, ...target() };
    vi.stubGlobal("document", page);
    vi.stubGlobal("window", { document: page, HTMLIFrameElement: class {}, localStorage: store, ...target() });
    vi.stubGlobal("navigator", { onLine: true });
    api.uploadDocument.mockImplementation(() => new Promise((resolve) => replies.push(resolve)));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });
  const sent = () => api.uploadDocument.mock.calls.map(([file]) => (file as File).name);
  const reply = (name: string) => ({
    document: { ...ready, id: `id-${name}`, display_name: name },
    duplicate: false,
    in_trash: false,
  });

  it("never sends a cancelled file: the next free turn goes to the file after it", async () => {
    const uploads = await mount();
    const picked = ["a", "b", "c", "d", "e"].map((name) => new File(["x"], `${name}.pdf`));
    await act(async () => void uploads.current().addFiles(picked));
    expect(sent()).toEqual(["a.pdf", "b.pdf", "c.pdf"]); // 3 at a time; d and e wait
    const state = (name: string) => uploads.current().entries.find((each) => each.name === name)?.state.kind;
    expect([state("d.pdf"), state("e.pdf")]).toEqual(["waiting", "waiting"]);

    const d = uploads.current().entries.find((each) => each.name === "d.pdf")!;
    await act(async () => uploads.current().cancel([d.key]));
    expect(uploads.current().entries.map((each) => each.name)).toEqual(["a.pdf", "b.pdf", "c.pdf", "e.pdf"]);

    await act(async () => replies[0]?.(reply("a.pdf"))); // a's turn ends: the next waiting file goes
    expect(sent()).toEqual(["a.pdf", "b.pdf", "c.pdf", "e.pdf"]);
    await act(async () => {
      replies[1]?.(reply("b.pdf"));
      replies[2]?.(reply("c.pdf"));
      replies[3]?.(reply("e.pdf"));
    });
    expect(sent()).not.toContain("d.pdf");
    expect(api.counted).not.toContain("d.pdf"); // its turn skipped it at once: not even counted
    expect(uploads.current().finished).toEqual({ uploaded: 4, already: 0, failed: 0 });
    await uploads.unmount();
  });

  it("never sends a file cancelled while its pages were being counted", async () => {
    let open = () => undefined as void;
    api.gate = new Promise<void>((resolve) => (open = resolve));
    const uploads = await mount();
    await act(async () => void uploads.current().addFiles([new File(["x"], "x.pdf")]));
    expect(api.counted).toEqual(["x.pdf"]);
    const [x] = uploads.current().entries;
    expect(x?.state.kind).toBe("waiting");
    await act(async () => uploads.current().cancel([x?.key ?? ""]));
    await act(async () => open());
    expect(sent()).toEqual([]);
    expect(uploads.current().entries).toEqual([]);
    await uploads.unmount();
  });

  it("remembers the drop without the cancelled files, so a reload says nothing of them", async () => {
    const uploads = await mount();
    const picked = ["a", "b", "c", "d", "e"].map((name) => new File(["x"], `${name}.pdf`));
    await act(async () => void uploads.current().addFiles(picked));
    const waitingKeys = uploads.current().entries.filter((each) => each.state.kind === "waiting").map((each) => each.key);
    expect(waitingKeys).toHaveLength(2);
    await act(async () => uploads.current().cancel(waitingKeys)); // Cancel all waiting
    expect(summaryText(trayCounts(uploads.current().entries))).toBe("Batch of 3 files · 3 sending");

    const kept = readStored(store, Date.now()); // as a reload reads it back
    expect(kept).toHaveLength(1);
    expect(kept[0]).toMatchObject({ already: 0, notSent: 0, cleared: [] });
    expect(JSON.parse(store.getItem(STORE_KEY) ?? "[]")).toHaveLength(1);
    await uploads.unmount();
  });

  it("forgets a drop whose every file was cancelled: the tray has no line for it, now or after a reload", async () => {
    const uploads = await mount();
    await act(async () => void uploads.current().addFiles(["a", "b", "c"].map((name) => new File(["x"], `${name}.pdf`))));
    await act(async () => void uploads.current().addFiles(["d", "e"].map((name) => new File(["x"], `${name}.pdf`))));
    expect(uploads.current().stored).toHaveLength(2);
    const later = uploads.current().entries.filter((each) => each.state.kind === "waiting");
    expect(later.map((each) => each.name)).toEqual(["d.pdf", "e.pdf"]); // the second drop waits whole
    await act(async () => uploads.current().cancel(later.map((each) => each.key)));
    const first = uploads.current().entries[0]?.batchId;
    expect(uploads.current().stored.map((batch) => batch.id)).toEqual([first]);
    expect(readStored(store, Date.now()).map((batch) => batch.id)).toEqual([first]);
    await uploads.unmount();
  });
});

// --- the tray's buttons ----------------------------------------------------------------

const TRAY = {
  entries: [] as UploadEntry[],
  views: {},
  stored: [],
  onClearFinished: () => undefined,
  onRestore: () => undefined,
  onRetry: () => undefined,
  onCancel: () => undefined,
  tree: buildTree([]),
  onUndone: () => undefined,
  canReread: true,
};

describe("the tray's Cancel buttons (#699)", () => {
  const tray = async (entries: UploadEntry[]) => {
    const { BatchTray } = await import("@/components/documents/UploadArea");
    return renderToStaticMarkup(createElement(BatchTray, { ...TRAY, entries }));
  };

  it("offers ✕ Cancel, named and titled for its file, on each waiting file and on no other", async () => {
    const html = await tray([entry("w1", waiting), entry("s", sending), entry("r", reading), entry("d", done), entry("e", failed)]);
    expect(html.match(/aria-label="Cancel upload of [a-z0-9]+\.pdf" title="Cancel upload of [a-z0-9]+\.pdf"/g)).toEqual([
      'aria-label="Cancel upload of w1.pdf" title="Cancel upload of w1.pdf"',
    ]);
    // Changed on purpose (#713): the button reads "Cancel all" (it covers files already sent too).
    expect(html).not.toContain(">Cancel all<"); // one waiting file: its own Cancel is enough
  });

  it("offers Cancel all waiting at the drop's top with two or more waiting, above its files", async () => {
    const html = await tray([entry("s", sending), entry("w1", waiting), entry("w2", waiting)]);
    expect(html).toContain(">Cancel all<"); // Changed on purpose (#713): "Cancel all"
    expect(html.indexOf(">Cancel all<")).toBeLessThan(html.indexOf(">s.pdf<"));
  });

  it("shows no Cancel when the tray is given no onCancel", async () => {
    const { BatchTray } = await import("@/components/documents/UploadArea");
    const html = renderToStaticMarkup(createElement(BatchTray, { ...TRAY, onCancel: undefined, entries: [entry("w1", waiting), entry("w2", waiting)] }));
    expect(html).not.toContain("Cancel");
  });
});

describe("pressing the tray's Cancel buttons (#699)", () => {
  let root: Root | null = null;
  afterEach(async () => {
    await act(async () => root?.unmount());
    root = null;
    vi.unstubAllGlobals();
  });

  /** The tray over `start`, with onCancel taking the files out as useUploads does. */
  async function pressable(start: UploadEntry[]) {
    const { install, fire } = await import("./fake-dom");
    const doc = install();
    const { BatchTray } = await import("@/components/documents/UploadArea");
    const { createRoot } = await import("react-dom/client");
    const asked: string[][] = [];
    let entries = start;
    const container = doc.body.appendChild(doc.createElement("div"));
    root = createRoot(container as unknown as Element);
    const draw = () => root!.render(createElement(BatchTray, { ...TRAY, entries, onCancel }));
    function onCancel(keys: string[]) {
      asked.push(keys);
      entries = entries.filter((each) => !keys.includes(each.key) || each.state.kind !== "waiting");
      draw();
    }
    await act(async () => draw());
    const button = (words: string) =>
      doc.body.all().find((each) => each.tagName === "BUTTON" && (each.getAttribute("aria-label") ?? each.textContent.trim()) === words)!;
    const press = (words: string) => act(async () => void fire(button(words), "click", { button: 0 }));
    return { doc, asked, button, press };
  }

  it("cancels one file at once, with no question, and focus moves to the next waiting file's Cancel", async () => {
    const tray = await pressable([entry("s", sending), entry("w1", waiting), entry("w2", waiting), entry("w3", waiting)]);
    await tray.press("Cancel upload of w2.pdf");
    expect(tray.asked).toEqual([["w2"]]);
    expect(tray.doc.body.all().some((each) => each.tagName === "DIALOG")).toBe(false);
    expect(tray.doc.activeElement?.getAttribute("aria-label")).toBe("Cancel upload of w3.pdf");
    await tray.press("Cancel upload of w3.pdf"); // the last in order: the one before it
    expect(tray.doc.activeElement?.getAttribute("aria-label")).toBe("Cancel upload of w1.pdf");
    await tray.press("Cancel upload of w1.pdf"); // none left waiting: the drop's line
    expect(tray.doc.activeElement?.textContent).toBe("Batch of 1 file · 1 sending");
  });

  it("the drop's only file cancelled: the drop goes, and focus goes to the tray's heading", async () => {
    const tray = await pressable([entry("w1", waiting), { ...entry("v", waiting), batchId: undefined }]);
    await tray.press("Cancel upload of w1.pdf");
    expect(tray.doc.body.textContent).not.toContain("Batch of");
    expect(tray.doc.activeElement?.textContent).toBe("Uploads");
  });

  it("Cancel all waiting asks once, Keep them first; Keep them keeps every file, Cancel files cancels them", async () => {
    const tray = await pressable([entry("s", sending), entry("w1", waiting), entry("w2", waiting)]);
    await tray.press("Cancel all"); // Changed on purpose (#713): "Cancel all"
    const dialog = tray.doc.body.all().find((each) => each.tagName === "DIALOG");
    expect(dialog?.textContent).toContain("Cancel 2 waiting files?");
    expect(tray.doc.activeElement?.textContent).toBe("Keep them");
    await tray.press("Keep them");
    expect(tray.asked).toEqual([]);
    expect(tray.doc.body.all().some((each) => each.tagName === "DIALOG")).toBe(false);

    await tray.press("Cancel all"); // Changed on purpose (#713): "Cancel all"
    await tray.press("Cancel files");
    expect(tray.asked).toEqual([["w1", "w2"]]);
    expect(tray.doc.body.textContent).toContain("Batch of 1 file · 1 sending"); // the line, counted at once
    expect(tray.doc.activeElement?.textContent).toBe("Batch of 1 file · 1 sending");
  });
});
