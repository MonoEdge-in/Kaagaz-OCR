// #713: Stop and Cancel a file already sent, while it waits on the server, is checked or is read: on
// its row (quiet Stop and Cancel, named for the file), for a drop (Stop all, Cancel all), and on the
// document page. Each asks first, the safe answer primary and focused. Stop keeps it in Documents
// ("Stopped by you", Read again); Cancel moves it to Trash ("Cancelled", Upload again). Files not
// sent yet are only cancelled (#699). The words and counts in node; the tray rendered and pressed
// in the fake DOM; the queue's calls through useUploads with a fake API, as upload-cancel-699.

import { act, createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  STOP_TEXT,
  canCancel,
  canStop,
  rowKind,
  statusBarText,
  summaryText,
  trayActionKeys,
  trayCounts,
  trayQuestion,
  trayResultText,
} from "@/components/documents/batchTray";
import { buildTree } from "@/components/documents/folderTree";
import { uploadStateText } from "@/components/documents/logic";
import type { UploadState } from "@/components/documents/logic";
import { EMPTY_QUEUE, queueReducer } from "@/components/documents/uploadQueue";
import type { UploadEntry } from "@/components/documents/uploadQueue";
import { settledText } from "@/components/viewer/reprocess";
import { statusChip } from "@/lib/format";
import { failureFix } from "@/lib/status";
import type { DocumentSummary } from "@/lib/types";

const api = vi.hoisted(() => ({
  uploadDocument: vi.fn(),
  stopDocument: vi.fn(),
  deleteDocument: vi.fn(),
  reprocessDocument: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  deleteDocument: api.deleteDocument,
  getAiSettings: () => new Promise(() => undefined),
  getBatch: () => new Promise(() => undefined),
  getUploadLimits: () => new Promise(() => undefined),
  isStorageFull: () => false,
  reprocessDocument: api.reprocessDocument,
  restoreDocument: vi.fn(),
  stopDocument: api.stopDocument,
  undoAutofile: vi.fn(),
  uploadDocument: api.uploadDocument,
  uploadVersion: vi.fn(),
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ me: { user: { id: "u1" } } }) }));
vi.mock("@/components/documents/pageCount", () => ({ checkPages: async () => ({ pages: null, problem: null }) }));

const BATCH = "6f1c2d3e-4a5b-4c6d-8e7f-901a2b3c4d5e";
const doc = (id: string, more: Partial<DocumentSummary> = {}) =>
  ({ id, display_name: `${id}.pdf`, status: "clean", failure_code: null, can_delete: true, status_message: "", ...more }) as DocumentSummary;
const reading = (id: string, more: Partial<DocumentSummary> = {}): UploadState => ({ kind: "reading", documentId: id, document: doc(id, more) });
const stopped = (id: string): UploadState => ({
  kind: "done",
  documentId: id,
  document: doc(id, { status: "failed", failure_code: "stopped", status_message: "Reading was stopped. Read it again to start it again." }),
});
const cancelled = (id: string): UploadState => ({ kind: "cancelled", documentId: id });
const waiting: UploadState = { kind: "waiting" };
const sending: UploadState = { kind: "sending", fraction: 0.4 };
const ready: UploadState = { kind: "done", documentId: "r", document: doc("r", { status: "ready" }) };
const entry = (key: string, state: UploadState): UploadEntry => ({ key, name: `${key}.pdf`, size: 10, batchId: BATCH, state });

describe("the words (#713)", () => {
  it("asks before Stop, one file named or a count, with the note and Keep reading", () => {
    expect(trayQuestion("stop", ["a.pdf"])).toEqual({
      title: "Stop a.pdf? It stays in Documents and you can read it again later.",
      note: "A page being read right now finishes first.",
      yes: "Stop file",
      no: "Keep reading",
    });
    expect(trayQuestion("stop", ["a.pdf", "b.pdf", "c.pdf"])).toEqual({
      title: "Stop 3 files? They stay in Documents and you can read them again later.",
      note: "A page being read right now finishes first.",
      yes: "Stop files",
      no: "Keep reading",
    });
  });

  it("asks before Cancel, one file named or a count, with the note and Keep it or Keep them", () => {
    expect(trayQuestion("cancel", ["a.pdf"])).toEqual({
      title: "Cancel a.pdf? It moves to Trash and you can upload it again.",
      note: "A page being read right now finishes first.",
      yes: "Cancel file",
      no: "Keep it",
    });
    expect(trayQuestion("cancel", ["a.pdf", "b.pdf"])).toEqual({
      title: "Cancel 2 files? They move to Trash and you can upload them again.",
      note: "A page being read right now finishes first.",
      yes: "Cancel files",
      no: "Keep them",
    });
  });

  it("keeps #699's question for files none of which was sent: they never reach Documents or Trash", () => {
    for (const action of ["stop", "cancel"] as const) {
      expect(trayQuestion(action, ["a.pdf", "b.pdf"], true)).toEqual({ title: "Cancel 2 waiting files?", note: null, yes: "Cancel files", no: "Keep them" });
    }
  });

  it("says what was done on the drop's line, each kind counted and in the singular or plural", () => {
    expect(trayResultText([entry("a", stopped("a")), entry("b", cancelled("b")), entry("c", cancelled("c"))])).toBe(
      "1 stopped. 2 cancelled and moved to Trash.",
    );
    expect(trayResultText(["a", "b", "c"].map((key) => entry(key, stopped(key))))).toBe("3 stopped.");
    expect(trayResultText(["a", "b", "c"].map((key) => entry(key, cancelled(key))))).toBe("3 cancelled and moved to Trash.");
    expect(trayResultText([entry("r", reading("r")), entry("d", ready)])).toBe(""); // nothing stopped or cancelled
    const refused = { action: "stop" as const, count: 1, why: "Only a file still waiting, being checked or being read can be stopped." };
    expect(trayResultText([entry("a", stopped("a"))], refused)).toBe(
      "1 stopped. 1 couldn't be stopped. Only a file still waiting, being checked or being read can be stopped.",
    );
  });

  it("names the rows' chips and lines, and the buttons for the file", () => {
    expect(uploadStateText(stopped("a"))).toBe("Stopped");
    expect(uploadStateText(cancelled("a"))).toBe("Cancelled");
    expect(STOP_TEXT.stoppedLine).toBe("Stopped by you. It stays in Documents.");
    expect(STOP_TEXT.cancelledLine).toBe("Cancelled. Moved to Trash.");
    expect(STOP_TEXT.stopOne("a.pdf")).toBe("Stop reading a.pdf");
    expect(STOP_TEXT.cancelOne("a.pdf")).toBe("Cancel a.pdf and move it to Trash");
  });

  it("shows a stopped file as Stopped by you, not Failed, wherever its status is shown, with Read again", () => {
    expect(statusChip("failed", "pdf", "stopped")).toEqual({ text: "Stopped by you", tone: "neutral", className: "status-chip" });
    expect(statusChip("failed", "pdf", "corrupt_file").text).toBe("Failed");
    expect(failureFix("failed", "stopped")).toBe("Read it again.");
    const detail = { status: "failed" as const, failure_code: "stopped", status_message: "Reading was stopped. Read it again to start it again.", values: [] };
    expect(settledText(detail, false)).toBe("Reading was stopped. Read it again to start it again."); // never "Reading failed"
  });
});

describe("the counts (#713)", () => {
  const rows = [entry("s", stopped("s")), entry("c1", cancelled("c1")), entry("c2", cancelled("c2")), entry("r", reading("r", { progress: { step: "reading", done: 0, total: 2 } }))];

  it("counts stopped and cancelled files on their own, never as waiting or reading", () => {
    expect(rows.map(rowKind)).toEqual(["stopped", "cancelled", "cancelled", "reading"]);
    expect(trayCounts(rows)).toMatchObject({ total: 4, stopped: 1, cancelled: 2, reading: 1, waiting: 0, failed: 0 });
    expect(summaryText(trayCounts(rows))).toBe("Batch of 4 files · 1 reading · 1 stopped · 2 cancelled");
  });

  it("says them in the status bar once nothing is going, never as failed", () => {
    const done = [entry("s", stopped("s")), entry("c", cancelled("c")), entry("d", ready)];
    expect(statusBarText(done, {}, 0)).toBe("Scan complete · 1 document · Nothing to check · 1 stopped · 1 cancelled");
  });

  it("sums a drop up without its cancelled files: they are in Trash, not uploaded", () => {
    let queue = queueReducer(EMPTY_QUEUE, { type: "add", entries: [entry("a", sending), entry("b", sending)] });
    queue = queueReducer(queue, { type: "change", key: "a", change: { state: cancelled("a") } });
    expect(queue.finished).toBeNull(); // b still sends
    queue = queueReducer(queue, { type: "change", key: "b", change: { state: stopped("b") } });
    expect(queue.finished).toEqual({ uploaded: 1, already: 0, failed: 0 });
  });
});

describe("which files may be stopped or cancelled (#713)", () => {
  it("Stop: a file on the server, waiting, checked or read, for who may read it again", () => {
    expect(canStop(entry("r", reading("r")), true)).toBe(true);
    expect(canStop(entry("r", reading("r")), false)).toBe(false); // a member
    for (const state of [waiting, sending, ready, stopped("s"), cancelled("c")]) expect(canStop(entry("x", state), true)).toBe(false);
  });

  it("Cancel: a file on the server, waiting, checked or read, for who may move it to Trash", () => {
    expect(canCancel(entry("r", reading("r")))).toBe(true);
    expect(canCancel(entry("r", reading("r", { can_delete: false })))).toBe(false);
    for (const state of [waiting, sending, ready, stopped("s"), cancelled("c")]) expect(canCancel(entry("x", state))).toBe(false);
  });

  it("Stop all and Cancel all take the files they may act on and every file still waiting", () => {
    const rows = [entry("w", waiting), entry("s", sending), entry("r1", reading("r1")), entry("r2", reading("r2", { can_delete: false })), entry("d", ready)];
    expect(trayActionKeys(rows, "stop", true)).toEqual({ keys: ["r1", "r2", "w"], shown: true });
    expect(trayActionKeys(rows, "cancel", true)).toEqual({ keys: ["r1", "w"], shown: true });
    expect(trayActionKeys(rows, "stop", false).shown).toBe(false); // a member: no Stop all
    // Only waiting files: Cancel all with two or more (#699), never Stop all.
    expect(trayActionKeys([entry("w", waiting)], "cancel", true).shown).toBe(false);
    expect(trayActionKeys([entry("w1", waiting), entry("w2", waiting)], "cancel", true)).toEqual({ keys: ["w1", "w2"], shown: true });
    expect(trayActionKeys([entry("w1", waiting), entry("w2", waiting)], "stop", true).shown).toBe(false);
  });
});

// --- the tray -----------------------------------------------------------------------------

const TRAY = {
  entries: [] as UploadEntry[],
  views: {},
  stored: [],
  onClearFinished: () => undefined,
  onRestore: () => undefined,
  onRetry: () => undefined,
  onCancel: () => undefined,
  onStopOrCancel: async () => ({ count: 0, why: "" }),
  onReadAgain: async () => null,
  onUploadAgain: () => undefined,
  tree: buildTree([]),
  onUndone: () => undefined,
  canReread: true,
};

describe("the tray's Stop and Cancel (#713)", () => {
  const tray = async (entries: UploadEntry[], more: Partial<typeof TRAY> = {}) => {
    const { BatchTray } = await import("@/components/documents/UploadArea");
    return renderToStaticMarkup(createElement(BatchTray, { ...TRAY, ...more, entries }));
  };

  it("offers quiet Stop and Cancel, named for the file, on a file being read; ✕ on a waiting one; nothing else", async () => {
    const html = await tray([entry("w", waiting), entry("s", sending), entry("r", reading("r")), entry("d", ready)]);
    expect(html.match(/aria-label="(?:Stop reading|Cancel)[^"]*"/g)).toEqual([
      'aria-label="Cancel upload of w.pdf"',
      'aria-label="Stop reading r.pdf"',
      'aria-label="Cancel r.pdf and move it to Trash"',
    ]);
    expect(html).toContain('aria-label="Stop reading r.pdf">Stop</button>');
    expect(html).toContain('aria-label="Cancel r.pdf and move it to Trash">Cancel</button>');
  });

  it("offers a member Cancel only, and no Cancel for a file they may not move to Trash", async () => {
    const member = await tray([entry("r", reading("r"))], { canReread: false });
    expect(member).not.toContain("Stop");
    expect(member).toContain('aria-label="Cancel r.pdf and move it to Trash"');
    const notTheirs = await tray([entry("r", reading("r", { can_delete: false }))], { canReread: false });
    expect(notTheirs).not.toContain("Cancel");
  });

  it("puts Stop all and Cancel all side by side under the drop's line, above its files", async () => {
    const html = await tray([entry("r", reading("r")), entry("w", waiting)]);
    const line = html.indexOf("Batch of 2 files");
    const stopAll = html.indexOf(">Stop all<");
    const cancelAll = html.indexOf(">Cancel all<");
    expect(line).toBeGreaterThan(0);
    expect(stopAll).toBeGreaterThan(line);
    expect(cancelAll).toBeGreaterThan(stopAll);
    expect(cancelAll).toBeLessThan(html.indexOf(">r.pdf<"));
    expect(await tray([entry("d", ready)])).not.toMatch(/Stop all|Cancel all/); // nothing to act on
  });

  it("says Stopped by you with Read again, and Cancelled with Upload again, and the drop's line", async () => {
    const html = await tray([entry("s", stopped("s")), entry("c", cancelled("c")), entry("c2", cancelled("c2"))]);
    expect(html).toContain('data-state="stopped"');
    expect(html).toContain('data-state="cancelled"');
    expect(html).toContain(">Stopped</span>");
    expect(html).toContain(">Cancelled</span>");
    expect(html).toContain("Stopped by you. It stays in Documents.");
    expect(html).toMatch(/Read again<span class="visually-hidden"> s\.pdf<\/span>/);
    expect(html).toContain("Cancelled. Moved to Trash.");
    expect(html).toMatch(/Upload again<span class="visually-hidden"> c\.pdf<\/span>/);
    expect(html).toContain('role="status">1 stopped. 2 cancelled and moved to Trash.</p>');
    expect(html).toContain("Batch of 3 files · 1 stopped · 2 cancelled");
    expect(html).not.toContain("Reading was stopped"); // the row's own line, not the server's
    expect(await tray([entry("s", stopped("s"))], { canReread: false })).not.toContain("Read again");
  });
});

describe("pressing the tray's Stop and Cancel (#713)", () => {
  let root: Root | null = null;
  afterEach(async () => {
    await act(async () => root?.unmount());
    root = null;
    vi.unstubAllGlobals();
  });

  async function pressable(start: UploadEntry[]) {
    const { install, fire } = await import("./fake-dom");
    const doc = install();
    vi.stubGlobal("self", { setTimeout, clearTimeout }); // next/link waits for an idle moment: a timer here
    const { BatchTray } = await import("@/components/documents/UploadArea");
    const { createRoot } = await import("react-dom/client");
    const calls: unknown[][] = [];
    let entries = start;
    const container = doc.body.appendChild(doc.createElement("div"));
    root = createRoot(container as unknown as Element);
    const draw = () => root!.render(createElement(BatchTray, { ...TRAY, entries, onCancel, onStopOrCancel }));
    function onCancel(keys: string[]) {
      calls.push(["cancel-waiting", keys]);
      entries = entries.filter((each) => !keys.includes(each.key));
      draw();
    }
    async function onStopOrCancel(action: "stop" | "cancel", keys: string[]) {
      calls.push([action, keys]);
      entries = entries.map((each) => (keys.includes(each.key) && each.state.kind === "reading" ? { ...each, state: action === "stop" ? stopped(each.key) : cancelled(each.key) } : each));
      draw();
      return { count: 0, why: "" };
    }
    await act(async () => draw());
    const button = (words: string) =>
      doc.body.all().find((each) => each.tagName === "BUTTON" && (each.getAttribute("aria-label") ?? each.textContent.trim()) === words)!;
    const press = (words: string) => act(async () => void fire(button(words), "click", { button: 0 }));
    const dialog = () => doc.body.all().find((each) => each.tagName === "DIALOG");
    return { doc, calls, press, dialog };
  }

  it("asks before one file's Stop, Keep reading first and primary; Keep reading keeps it", async () => {
    const tray = await pressable([entry("r", reading("r"))]);
    await tray.press("Stop reading r.pdf");
    expect(tray.dialog()?.textContent).toContain("Stop r.pdf? It stays in Documents and you can read it again later.");
    expect(tray.dialog()?.textContent).toContain("A page being read right now finishes first.");
    expect(tray.doc.activeElement?.textContent).toBe("Keep reading");
    expect(tray.doc.activeElement?.getAttribute("class")).toContain("btn-primary");
    await tray.press("Keep reading");
    expect(tray.calls).toEqual([]);
    expect(tray.dialog()).toBeUndefined();
  });

  it("Stop file stops it: the row says so, with Read again, and focus goes to the drop's line", async () => {
    const tray = await pressable([entry("r", reading("r")), entry("r2", reading("r2"))]);
    await tray.press("Stop reading r.pdf");
    await tray.press("Stop file");
    expect(tray.calls).toEqual([["stop", ["r"]]]);
    expect(tray.doc.body.textContent).toContain("Stopped by you. It stays in Documents.");
    expect(tray.doc.body.textContent).toContain("1 stopped.");
    expect(tray.doc.activeElement?.textContent).toContain("Batch of 2 files");
  });

  it("one file's Cancel asks with Keep it, and Cancel file moves it to Trash", async () => {
    const tray = await pressable([entry("r", reading("r"))]);
    await tray.press("Cancel r.pdf and move it to Trash");
    expect(tray.dialog()?.textContent).toContain("Cancel r.pdf? It moves to Trash and you can upload it again.");
    expect(tray.doc.activeElement?.textContent).toBe("Keep it");
    const yes = tray.doc.body.all().find((each) => each.tagName === "BUTTON" && each.textContent.trim() === "Cancel file");
    expect(yes?.getAttribute("class")).toContain("btn-danger");
    await tray.press("Cancel file");
    expect(tray.calls).toEqual([["cancel", ["r"]]]);
    expect(tray.doc.body.textContent).toContain("Cancelled. Moved to Trash.");
    expect(tray.doc.body.textContent).toContain("1 cancelled and moved to Trash.");
  });

  it("Stop all asks once for every file it covers; the waiting ones are only cancelled (#699)", async () => {
    const tray = await pressable([entry("r1", reading("r1")), entry("w", waiting), entry("r2", reading("r2"))]);
    await tray.press("Stop all");
    expect(tray.dialog()?.textContent).toContain("Stop 3 files? They stay in Documents and you can read them again later.");
    await tray.press("Stop files");
    expect(tray.calls).toEqual([
      ["cancel-waiting", ["w"]],
      ["stop", ["r1", "r2", "w"]],
    ]);
    expect(tray.doc.body.textContent).toContain("2 stopped.");
    expect(tray.doc.body.textContent).toContain("Batch of 2 files · 2 stopped");
  });

  it("Cancel all asks once, Keep them first, and cancels every file it covers", async () => {
    const tray = await pressable([entry("r1", reading("r1")), entry("r2", reading("r2"))]);
    await tray.press("Cancel all");
    expect(tray.dialog()?.textContent).toContain("Cancel 2 files? They move to Trash and you can upload them again.");
    expect(tray.doc.activeElement?.textContent).toBe("Keep them");
    await tray.press("Cancel files");
    expect(tray.calls).toEqual([["cancel", ["r1", "r2"]]]);
    expect(tray.doc.body.textContent).toContain("2 cancelled and moved to Trash.");
  });
});

// --- the queue's calls ----------------------------------------------------------------------

type Uploads = ReturnType<typeof import("@/components/documents/UploadArea").useUploads>;
const target = () => ({ addEventListener: () => undefined, removeEventListener: () => undefined });

async function mount() {
  const { useUploads } = await import("@/components/documents/UploadArea");
  const { createRoot } = await import("react-dom/client");
  let latest: Uploads | null = null;
  function Probe() {
    latest = useUploads(() => undefined);
    return null;
  }
  const container = { nodeType: 1, tagName: "DIV", namespaceURI: null, textContent: "", ownerDocument: null, ...target() };
  const probe = createRoot(container as unknown as Element);
  await act(async () => probe.render(createElement(Probe)));
  return {
    current: () => {
      if (latest === null) throw new Error("not rendered");
      return latest;
    },
    unmount: () => act(() => probe.unmount()),
  };
}

describe("stopping and cancelling through the queue (#713)", () => {
  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const page = { visibilityState: "visible", activeElement: null, ...target() };
    vi.stubGlobal("document", page);
    vi.stubGlobal("window", { document: page, HTMLIFrameElement: class {}, localStorage: null, ...target() });
    vi.stubGlobal("navigator", { onLine: true });
    api.uploadDocument.mockImplementation(async (file: File) => ({
      document: doc(`id-${file.name}`, { display_name: file.name }),
      duplicate: false,
      in_trash: false,
    }));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  async function three() {
    const uploads = await mount();
    await act(async () => void uploads.current().addFiles(["a", "b", "c"].map((name) => new File(["x"], `${name}.pdf`))));
    const keys = uploads.current().entries.map((each) => each.key);
    expect(uploads.current().entries.map((each) => each.state.kind)).toEqual(["reading", "reading", "reading"]);
    return { uploads, keys };
  }

  it("stops each file with one call, and each row then says Stopped", async () => {
    const { uploads, keys } = await three();
    api.stopDocument.mockImplementation(async (id: string) => doc(id, { status: "failed", failure_code: "stopped" }));
    let refused: unknown;
    await act(async () => void (refused = await uploads.current().stopOrCancel("stop", keys)));
    expect(api.stopDocument.mock.calls.map(([id]) => id)).toEqual(["id-a.pdf", "id-b.pdf", "id-c.pdf"]);
    expect(refused).toEqual({ count: 0, why: "" });
    expect(uploads.current().entries.map((each) => uploadStateText(each.state))).toEqual(["Stopped", "Stopped", "Stopped"]);
    await uploads.unmount();
  });

  it("cancels each file through Move to Trash, and says what the server refused, file by file", async () => {
    const { ApiError } = await import("@/lib/api");
    const { uploads, keys } = await three();
    api.deleteDocument.mockImplementation(async (id: string) => {
      // The fake ApiError (vi.mock above) takes the words alone.
      if (id === "id-b.pdf") throw new (ApiError as unknown as new (message: string) => Error)("Only who uploaded every version, or an admin, can do that.");
    });
    let refused: unknown;
    await act(async () => void (refused = await uploads.current().stopOrCancel("cancel", keys)));
    expect(api.deleteDocument.mock.calls.map(([id]) => id)).toEqual(["id-a.pdf", "id-b.pdf", "id-c.pdf"]);
    expect(refused).toEqual({ count: 1, why: "Only who uploaded every version, or an admin, can do that." });
    expect(uploads.current().entries.map((each) => each.state.kind)).toEqual(["cancelled", "reading", "cancelled"]);
    await uploads.unmount();
  });

  it("Read again on a stopped row follows the file again while it is read", async () => {
    const { uploads, keys } = await three();
    api.stopDocument.mockImplementation(async (id: string) => doc(id, { status: "failed", failure_code: "stopped" }));
    await act(async () => void (await uploads.current().stopOrCancel("stop", [keys[0]!])));
    api.reprocessDocument.mockResolvedValue(doc("id-a.pdf", { status: "received" }));
    let why: unknown = "unset";
    await act(async () => void (why = await uploads.current().readAgain(keys[0]!, "id-a.pdf")));
    expect(why).toBeNull();
    expect(api.reprocessDocument).toHaveBeenCalledWith("id-a.pdf");
    expect(uploads.current().entries[0]?.state.kind).toBe("reading");
    await uploads.unmount();
  });
});

describe("Stop and Cancel on the document page (#713)", () => {
  const page = async (canStop: boolean, canCancel: boolean) => {
    const { StopCancel } = await import("@/components/documents/StopCancel");
    return renderToStaticMarkup(createElement(StopCancel, { documentId: "d", name: "a.pdf", canStop, canCancel, onDone: () => undefined }));
  };

  it("offers Stop to who may read it again and Cancel to who may move it to Trash, named for the file", async () => {
    const both = await page(true, true);
    expect(both).toContain('aria-label="Stop reading a.pdf">Stop</button>');
    expect(both).toContain('aria-label="Cancel a.pdf and move it to Trash">Cancel</button>');
    expect(await page(false, true)).not.toContain("Stop");
    expect(await page(true, false)).not.toContain("Cancel");
    expect(await page(false, false)).toBe("");
  });
});
