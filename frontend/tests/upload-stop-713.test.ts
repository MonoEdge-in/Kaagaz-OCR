// #713: Stop and Cancel a file already sent, while it waits on the server, is checked or is read for
// the first time: on its row (quiet Stop and Cancel, named for the file), for a drop (Stop all,
// Cancel all), and on the document page. Each asks first, the safe answer primary and focused. Stop
// keeps it in Documents ("Stopped by you" on the row of who pressed it, "Stopped" elsewhere, Read
// again); Cancel moves it to Trash ("Cancelled", Upload again). Files not sent yet are only cancelled
// (#699); files still being sent are stopped or cancelled once stored. A file read before is never
// stopped (Tushar, 20 Nov 2026). The words and counts in node; the tray and the document page's
// buttons rendered and pressed in the fake DOM; the queue's calls through useUploads with a fake API.

import { readFileSync } from "node:fs";
import { join } from "node:path";
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

// As lib/api's ApiError: the status, the code, the server's words and its details (hoisted with vi.mock).
const { FakeApiError } = vi.hoisted(() => ({
  FakeApiError: class extends Error {
    constructor(
      readonly status: number,
      readonly code: string,
      message: string,
      readonly details?: unknown,
    ) {
      super(message);
    }
  },
}));

vi.mock("@/lib/api", () => ({
  ApiError: FakeApiError,
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
const STOPPED_MESSAGE = "Reading was stopped. Read it again to start it again.";
const doc = (id: string, more: Partial<DocumentSummary> = {}) =>
  ({ id, display_name: `${id}.pdf`, status: "clean", failure_code: null, can_delete: true, version_no: 1, status_message: "", ...more }) as DocumentSummary;
const reading = (id: string, more: Partial<DocumentSummary> = {}): UploadState => ({ kind: "reading", documentId: id, document: doc(id, more) });
const stoppedDoc = (id: string) => doc(id, { status: "failed", failure_code: "stopped", status_message: STOPPED_MESSAGE });
const stopped = (id: string): UploadState => ({ kind: "done", documentId: id, document: stoppedDoc(id) });
const cancelled = (id: string): UploadState => ({ kind: "cancelled", documentId: id });
const waiting: UploadState = { kind: "waiting" };
const sending: UploadState = { kind: "sending", fraction: 0.4 };
const ready: UploadState = { kind: "done", documentId: "r", document: doc("r", { status: "ready" }) };
const entry = (key: string, state: UploadState, more: Partial<UploadEntry> = {}): UploadEntry => ({
  key,
  name: `${key}.pdf`,
  size: 10,
  batchId: BATCH,
  state,
  ...more,
});
const mine = (key: string) => entry(key, stopped(key), { byYou: true }); // stopped from this tab

describe("the words (#713)", () => {
  it("asks before Stop, one file named or a count, with the note and Keep reading", () => {
    expect(trayQuestion("stop", ["a.pdf"])).toEqual({
      title: "Stop a.pdf? It stays in Documents and you can read it again later.",
      note: "A page being read right now finishes first.",
      notSent: null,
      yes: "Stop file",
      no: "Keep reading",
    });
    expect(trayQuestion("stop", ["a.pdf", "b.pdf", "c.pdf"])).toMatchObject({
      title: "Stop 3 files? They stay in Documents and you can read them again later.",
      yes: "Stop files",
      no: "Keep reading",
    });
  });

  it("asks before Cancel, one file named or a count, with the note and Keep it or Keep them", () => {
    expect(trayQuestion("cancel", ["a.pdf"])).toEqual({
      title: "Cancel a.pdf? It moves to Trash and you can upload it again.",
      note: "A page being read right now finishes first.",
      notSent: null,
      yes: "Cancel file",
      no: "Keep it",
    });
    expect(trayQuestion("cancel", ["a.pdf", "b.pdf"])).toMatchObject({
      title: "Cancel 2 files? They move to Trash and you can upload them again.",
      yes: "Cancel files",
      no: "Keep them",
    });
  });

  it("counts only the files sent in the question, and says the ones not sent yet are cancelled", () => {
    expect(trayQuestion("stop", ["a.pdf", "b.pdf", "c.pdf"], 2)).toMatchObject({
      title: "Stop 3 files? They stay in Documents and you can read them again later.",
      notSent: "2 not sent yet are cancelled.",
    });
    expect(trayQuestion("cancel", ["a.pdf"], 1)).toMatchObject({
      title: "Cancel a.pdf? It moves to Trash and you can upload it again.",
      notSent: "1 not sent yet is cancelled.",
    });
  });

  it("keeps #699's question when none was sent: they never reach Documents or Trash", () => {
    for (const action of ["stop", "cancel"] as const) {
      expect(trayQuestion(action, [], 2)).toEqual({ title: "Cancel 2 waiting files?", note: null, notSent: null, yes: "Cancel files", no: "Keep them" });
    }
  });

  it("says what was done here on the drop's line, each kind counted and in the singular or plural", () => {
    expect(trayResultText([mine("a"), entry("b", cancelled("b")), entry("c", cancelled("c"))])).toBe(
      "1 stopped. 2 cancelled and moved to Trash.",
    );
    expect(trayResultText(["a", "b", "c"].map(mine))).toBe("3 stopped.");
    expect(trayResultText(["a", "b", "c"].map((key) => entry(key, cancelled(key))))).toBe("3 cancelled and moved to Trash.");
    expect(trayResultText([entry("s", stopped("s")), entry("d", ready)])).toBe(""); // stopped by someone else
    const refused = { action: "stop" as const, count: 1, why: "This file was already read. Reading again will finish." };
    expect(trayResultText([mine("a")], refused)).toBe("1 stopped. 1 couldn't be stopped. This file was already read. Reading again will finish.");
  });

  it("names the rows' chips and lines, and the buttons for the file", () => {
    expect(uploadStateText(stopped("a"))).toBe("Stopped");
    expect(uploadStateText(cancelled("a"))).toBe("Cancelled");
    expect(STOP_TEXT.stoppedLine).toBe("Stopped by you. It stays in Documents.");
    expect(STOP_TEXT.stoppedPage).toBe("Stopped. It stays in Documents.");
    expect(STOP_TEXT.cancelledLine).toBe("Cancelled. Moved to Trash.");
    expect(STOP_TEXT.cooldown).toBe("You can read it again in 10 minutes.");
    expect(STOP_TEXT.stopOne("a.pdf")).toBe("Stop reading a.pdf");
    expect(STOP_TEXT.cancelOne("a.pdf")).toBe("Cancel a.pdf and move it to Trash");
  });

  it("shows a stopped file as Stopped, not Failed, wherever its status chip is drawn (Tushar, 20 Nov 2026)", () => {
    expect(statusChip("failed", "pdf", "stopped")).toEqual({ text: "Stopped", tone: "neutral", className: "status-chip" });
    expect(statusChip("failed", "pdf", "corrupt_file").text).toBe("Failed");
    expect(failureFix("failed", "stopped")).toBe("Read it again.");
    const detail = { status: "failed" as const, failure_code: "stopped", status_message: STOPPED_MESSAGE, values: [] };
    expect(settledText(detail, false)).toBe(STOPPED_MESSAGE); // never "Reading failed"
  });
});

describe("the counts (#713)", () => {
  const rows = [mine("s"), entry("c1", cancelled("c1")), entry("c2", cancelled("c2")), entry("r", reading("r", { progress: { step: "reading", done: 0, total: 2 } }))];

  it("counts stopped and cancelled files on their own, never as waiting or reading", () => {
    expect(rows.map(rowKind)).toEqual(["stopped", "cancelled", "cancelled", "reading"]);
    expect(trayCounts(rows)).toMatchObject({ total: 4, stopped: 1, cancelled: 2, reading: 1, waiting: 0, failed: 0 });
    expect(summaryText(trayCounts(rows))).toBe("Batch of 4 files · 1 reading · 1 stopped · 2 cancelled");
  });

  it("says them in the status bar once nothing is going, never as failed", () => {
    const done = [mine("s"), entry("c", cancelled("c")), entry("d", ready)];
    expect(statusBarText(done, {}, 0)).toBe("Scan complete · 1 document · Nothing to check · 1 stopped · 1 cancelled");
  });

  it("says a file of an organisation turned off as stopped in the status bar too, not failed", () => {
    const off: UploadState = { kind: "done", documentId: "o", document: doc("o", { status: "failed", failure_code: "customer_disabled" }) };
    expect(statusBarText([entry("o", off), entry("d", ready)], {}, 0)).toBe("Scan complete · 1 document · Nothing to check · 1 stopped");
  });

  it("sums a drop up without its cancelled files: they are in Trash, not uploaded", () => {
    let queue = queueReducer(EMPTY_QUEUE, { type: "add", entries: [entry("a", sending), entry("b", sending)] });
    queue = queueReducer(queue, { type: "change", key: "a", change: { state: cancelled("a") } });
    expect(queue.finished).toBeNull(); // b still sends
    queue = queueReducer(queue, { type: "change", key: "b", change: { state: stopped("b") } });
    expect(queue.finished).toEqual({ uploaded: 1, already: 0, failed: 0 });
  });

  it("follows a cancelled file again once its batch lists it, restored from Trash: it is Stopped, with Read again", () => {
    let queue = queueReducer(EMPTY_QUEUE, { type: "add", entries: [entry("c", cancelled("id-c"))] });
    queue = queueReducer(queue, { type: "batch", batchId: BATCH, documents: [], add: false }); // in Trash: not listed
    expect(queue.entries[0]?.state.kind).toBe("cancelled");
    queue = queueReducer(queue, { type: "batch", batchId: BATCH, documents: [stoppedDoc("id-c")], add: false });
    expect(queue.entries[0]?.state).toEqual({ kind: "done", documentId: "id-c", document: stoppedDoc("id-c") });
    expect(rowKind(queue.entries[0]!)).toBe("stopped");
  });

  it("keeps a cancelled row cancelled when a poll read before the Cancel still says it is being read", () => {
    let queue = queueReducer(EMPTY_QUEUE, { type: "add", entries: [entry("c", cancelled("id-c"))] });
    queue = queueReducer(queue, { type: "batch", batchId: BATCH, documents: [doc("id-c", { status: "clean" })], add: false });
    expect(queue.entries[0]?.state).toEqual(cancelled("id-c")); // never "Reading…" again: later polls skip it (Trash)
    expect(queue.finished).toEqual({ uploaded: 0, already: 0, failed: 0 }); // the drop settles
  });

  it("keeps 'by you' on a row stopped here when a poll read before the Stop still says it is being read", () => {
    let queue = queueReducer(EMPTY_QUEUE, { type: "add", entries: [mine("s")] });
    queue = queueReducer(queue, { type: "batch", batchId: BATCH, documents: [doc("s", { status: "received" })], add: false });
    queue = queueReducer(queue, { type: "batch", batchId: BATCH, documents: [stoppedDoc("s")], add: false });
    expect(queue.entries[0]?.byYou).toBe(true);
    expect(trayResultText(queue.entries)).toBe("1 stopped."); // counted as stopped by you
  });
});

describe("which files may be stopped or cancelled (#713)", () => {
  it("Stop: a file on the server, read for the first time, or still being sent, for who may read it again", () => {
    expect(canStop(entry("r", reading("r")), true)).toBe(true);
    expect(canStop(entry("s", sending), true)).toBe(true);
    expect(canStop(entry("r", reading("r")), false)).toBe(false); // a member
    expect(canStop(entry("r", reading("r", { read_before: true })), true)).toBe(false); // read before: it goes on
    for (const state of [waiting, ready, stopped("s"), cancelled("c")]) expect(canStop(entry("x", state), true)).toBe(false);
  });

  it("Cancel: a file of one version on the server, read for the first time, or still being sent, for who may move it to Trash", () => {
    expect(canCancel(entry("r", reading("r")))).toBe(true);
    expect(canCancel(entry("s", sending))).toBe(true);
    expect(canCancel(entry("r", reading("r", { can_delete: false })))).toBe(false);
    expect(canCancel(entry("r", reading("r", { read_before: true })))).toBe(false);
    // Trash takes every version: a new version is never cancelled from the tray.
    expect(canCancel(entry("v", reading("v", { version_no: 2 })))).toBe(false);
    expect(canCancel(entry("v", reading("v"), { versionOf: "deed.pdf" }))).toBe(false);
    for (const state of [waiting, ready, stopped("s"), cancelled("c")]) expect(canCancel(entry("x", state))).toBe(false);
  });

  it("Stop all and Cancel all take the files they may act on, those still being sent, and every file waiting", () => {
    const rows = [entry("w", waiting), entry("s", sending), entry("r1", reading("r1")), entry("r2", reading("r2", { can_delete: false })), entry("d", ready)];
    expect(trayActionKeys(rows, "stop", true)).toEqual({ keys: ["s", "r1", "r2", "w"], shown: true });
    expect(trayActionKeys(rows, "cancel", true)).toEqual({ keys: ["s", "r1", "w"], shown: true });
    expect(trayActionKeys(rows, "stop", false).shown).toBe(false); // a member: no Stop all
    expect(trayActionKeys([entry("w", waiting)], "cancel", true).shown).toBe(false);
    expect(trayActionKeys([entry("w1", waiting), entry("w2", waiting)], "cancel", true)).toEqual({ keys: ["w1", "w2"], shown: true });
    expect(trayActionKeys([entry("w1", waiting), entry("w2", waiting)], "stop", true).shown).toBe(false);
  });

  it("offers the document page's Cancel only for a file of one version, and nothing for one read before", () => {
    const view = readFileSync(join(process.cwd(), "app/(app)/documents/[id]/DocumentView.tsx"), "utf8");
    expect(view).toContain("beingRead(detail.status) && detail.deleted_at === null && detail.read_before !== true && (");
    expect(view).toContain("canCancel={detail.can_delete && detail.versions.length <= 1}");
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

  it("says a file asked to stop or cancel while it is still being sent will be, once sent", async () => {
    const html = await tray([entry("s", sending, { asked: "stop" }), entry("t", sending, { asked: "cancel" }), entry("u", sending)]);
    expect(html).toContain("Will stop once sent.");
    expect(html).toContain("Will be cancelled once sent.");
    expect(html.match(/once sent\./g)).toHaveLength(2); // u was not asked
  });

  it("offers nothing on a file read before, being read again", async () => {
    expect(await tray([entry("r", reading("r", { read_before: true }))])).not.toMatch(/Stop|Cancel/);
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

  it("says Stopped by you on the row of who pressed Stop here, Cancelled with Upload again, and the drop's line", async () => {
    const html = await tray([mine("s"), entry("c", cancelled("c")), entry("c2", cancelled("c2"))]);
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
    expect(await tray([mine("s")], { canReread: false })).not.toContain("Read again");
  });

  it("says a file stopped by someone else in the server's words, with Read again, never 'by you'", async () => {
    const html = await tray([entry("s", stopped("s"))]);
    expect(html).toContain(">Stopped</span>");
    expect(html).toContain(STOPPED_MESSAGE);
    expect(html).not.toContain("Stopped by you");
    expect(html).toMatch(/Read again<span class="visually-hidden"> s\.pdf<\/span>/);
  });
});

describe("pressing the tray's Stop and Cancel (#713)", () => {
  let root: Root | null = null;
  afterEach(async () => {
    await act(async () => root?.unmount());
    root = null;
    vi.unstubAllGlobals();
  });

  async function pressable(start: UploadEntry[], refused = { count: 0, why: "" }) {
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
      entries = entries.map((each) =>
        keys.includes(each.key) && each.state.kind === "reading" && refused.count === 0
          ? { ...each, state: action === "stop" ? stopped(each.key) : cancelled(each.key), byYou: action === "stop" }
          : each,
      );
      draw();
      return refused;
    }
    await act(async () => draw());
    const button = (words: string) =>
      doc.body.all().find((each) => each.tagName === "BUTTON" && (each.getAttribute("aria-label") ?? each.textContent.trim()) === words)!;
    const press = (words: string) => act(async () => void fire(button(words), "click", { button: 0 }));
    const dialog = () => doc.body.all().find((each) => each.tagName === "DIALOG");
    const turn = async (key: string, state: UploadState) => {
      entries = entries.map((each) => (each.key === key ? { ...each, state } : each));
      await act(async () => draw());
    };
    return { doc, calls, press, dialog, turn };
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

  it("says a refused Stop on the drop's line, in the server's words", async () => {
    const why = "This file was already read. Reading again will finish.";
    const tray = await pressable([entry("r", reading("r"))], { count: 1, why });
    await tray.press("Stop reading r.pdf");
    await tray.press("Stop file");
    const line = tray.doc.body.all().find((each) => each.getAttribute("role") === "status");
    expect(line?.textContent).toBe(`1 couldn't be stopped. ${why}`);
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

  it("Stop all counts only the files sent, says the waiting ones are cancelled, and cancels those (#699)", async () => {
    const tray = await pressable([entry("r1", reading("r1")), entry("w", waiting), entry("r2", reading("r2"))]);
    await tray.press("Stop all");
    expect(tray.dialog()?.textContent).toContain("Stop 2 files? They stay in Documents and you can read them again later.");
    expect(tray.dialog()?.textContent).toContain("1 not sent yet is cancelled.");
    await tray.press("Stop files");
    expect(tray.calls).toEqual([
      ["cancel-waiting", ["w"]],
      ["stop", ["r1", "r2", "w"]],
    ]);
    expect(tray.doc.body.textContent).toContain("2 stopped.");
    expect(tray.doc.body.textContent).toContain("Batch of 2 files · 2 stopped");
  });

  it("a waiting file that starts sending while the question is open is asked to stop too, not skipped", async () => {
    const tray = await pressable([entry("r", reading("r")), entry("w", waiting)]);
    await tray.press("Stop all");
    await tray.turn("w", sending); // its turn came while the question was open
    await tray.press("Stop files");
    expect(tray.calls).toEqual([["stop", ["r", "w"]]]); // not cancelled as waiting: stopped once stored
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

// --- the document page ------------------------------------------------------------------------

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

  describe("pressed", () => {
    let root: Root | null = null;
    afterEach(async () => {
      await act(async () => root?.unmount());
      root = null;
      vi.unstubAllGlobals();
      vi.clearAllMocks();
    });

    async function pressable() {
      const { install, fire } = await import("./fake-dom");
      const doc = install();
      const { StopCancel } = await import("@/components/documents/StopCancel");
      const { createRoot } = await import("react-dom/client");
      const done: string[] = [];
      const container = doc.body.appendChild(doc.createElement("div"));
      root = createRoot(container as unknown as Element);
      await act(async () =>
        root!.render(createElement(StopCancel, { documentId: "d", name: "a.pdf", canStop: true, canCancel: true, onDone: (m: string) => done.push(m) })),
      );
      const button = (words: string) =>
        doc.body.all().find((each) => each.tagName === "BUTTON" && (each.getAttribute("aria-label") ?? each.textContent.trim()) === words)!;
      const press = (words: string) => act(async () => void fire(button(words), "click", { button: 0 }));
      return { doc, done, press };
    }

    it("Stop asks, Keep reading primary and focused; Stop file calls Stop, never Trash, and says so", async () => {
      api.stopDocument.mockResolvedValue(stoppedDoc("d"));
      const page = await pressable();
      await page.press("Stop reading a.pdf");
      expect(page.doc.activeElement?.textContent).toBe("Keep reading");
      expect(page.doc.activeElement?.getAttribute("class")).toContain("btn-primary");
      await page.press("Stop file");
      expect(api.stopDocument).toHaveBeenCalledWith("d");
      expect(api.deleteDocument).not.toHaveBeenCalled();
      expect(page.done).toEqual(["Stopped. It stays in Documents."]);
    });

    it("says a refused Stop in the server's words", async () => {
      api.stopDocument.mockRejectedValue(new FakeApiError(409, "already_read", "This file was already read. Reading again will finish."));
      const page = await pressable();
      await page.press("Stop reading a.pdf");
      await page.press("Stop file");
      const alert = page.doc.body.all().find((each) => each.getAttribute("role") === "alert");
      expect(alert?.textContent).toBe("This file was already read. Reading again will finish.");
      expect(page.done).toEqual([]);
    });
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
  let replies: ((value: unknown) => void)[] = [];
  beforeEach(() => {
    replies = [];
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

  it("stops each file with one call, and each row then says Stopped, by you", async () => {
    const { uploads, keys } = await three();
    api.stopDocument.mockImplementation(async (id: string) => stoppedDoc(id));
    let refused: unknown;
    await act(async () => void (refused = await uploads.current().stopOrCancel("stop", keys)));
    expect(api.stopDocument.mock.calls.map(([id]) => id)).toEqual(["id-a.pdf", "id-b.pdf", "id-c.pdf"]);
    expect(refused).toEqual({ count: 0, why: "" });
    expect(uploads.current().entries.map((each) => [uploadStateText(each.state), each.byYou])).toEqual([
      ["Stopped", true],
      ["Stopped", true],
      ["Stopped", true],
    ]);
    await uploads.unmount();
  });

  it("counts a refused Stop with the server's words, and a lost connection in plain words", async () => {
    const { uploads, keys } = await three();
    api.stopDocument.mockImplementation(async (id: string) => {
      if (id === "id-a.pdf") throw new FakeApiError(409, "already_read", "This file was already read. Reading again will finish.");
      if (id === "id-b.pdf") throw new TypeError("Failed to fetch");
      return stoppedDoc(id);
    });
    let refused: unknown;
    await act(async () => void (refused = await uploads.current().stopOrCancel("stop", keys)));
    expect(refused).toEqual({ count: 2, why: "This file was already read. Reading again will finish." });
    api.stopDocument.mockRejectedValue(new TypeError("Failed to fetch"));
    await act(async () => void (refused = await uploads.current().stopOrCancel("stop", [keys[0]!])));
    expect(refused).toEqual({ count: 1, why: "Check your connection, then try again." });
    await uploads.unmount();
  });

  it("cancels each file through Move to Trash, and says what the server refused, file by file", async () => {
    const { uploads, keys } = await three();
    api.deleteDocument.mockImplementation(async (id: string) => {
      if (id === "id-b.pdf") throw new FakeApiError(403, "forbidden", "Only who uploaded every version, or an admin, can do that.");
    });
    let refused: unknown;
    await act(async () => void (refused = await uploads.current().stopOrCancel("cancel", keys)));
    expect(api.deleteDocument.mock.calls.map(([id]) => id)).toEqual(["id-a.pdf", "id-b.pdf", "id-c.pdf"]);
    expect(refused).toEqual({ count: 1, why: "Only who uploaded every version, or an admin, can do that." });
    expect(uploads.current().entries.map((each) => each.state.kind)).toEqual(["cancelled", "reading", "cancelled"]);
    await uploads.unmount();
  });

  it("stops a file still being sent once it is stored, and only then says what was done", async () => {
    api.uploadDocument.mockImplementation(() => new Promise((resolve) => replies.push(resolve)));
    api.stopDocument.mockImplementation(async (id: string) => stoppedDoc(id));
    const uploads = await mount();
    await act(async () => void uploads.current().addFiles([new File(["x"], "a.pdf")]));
    const [a] = uploads.current().entries;
    expect(a?.state.kind).toBe("sending");
    let refused: unknown = "pending";
    let asking: Promise<unknown> = Promise.resolve();
    await act(async () => void (asking = uploads.current().stopOrCancel("stop", [a!.key]).then((r) => (refused = r))));
    expect(refused).toBe("pending"); // waits for the file to be stored
    expect(api.stopDocument).not.toHaveBeenCalled();
    await act(async () => {
      replies[0]?.({ document: doc("id-a"), duplicate: false, in_trash: false });
      await asking;
    });
    expect(api.stopDocument).toHaveBeenCalledWith("id-a");
    expect(refused).toEqual({ count: 0, why: "" });
    expect(uploadStateText(uploads.current().entries[0]!.state)).toBe("Stopped");
    await uploads.unmount();
  });

  it("notes every file still being sent before the first call, so one stored meanwhile is still stopped", async () => {
    // a is stored at once; b's upload waits. a's Stop is held while b finishes sending.
    api.uploadDocument.mockImplementation((file: File) =>
      file.name === "a.pdf"
        ? Promise.resolve({ document: doc("id-a"), duplicate: false, in_trash: false })
        : new Promise((resolve) => replies.push(resolve)),
    );
    let releaseA = () => undefined as void;
    api.stopDocument.mockImplementation((id: string) =>
      id === "id-a" ? new Promise((resolve) => (releaseA = () => resolve(stoppedDoc(id)))) : Promise.resolve(stoppedDoc(id)),
    );
    const uploads = await mount();
    await act(async () => void uploads.current().addFiles([new File(["x"], "a.pdf"), new File(["x"], "b.pdf")]));
    const [a, b] = uploads.current().entries;
    expect([a?.state.kind, b?.state.kind]).toEqual(["reading", "sending"]);
    expect(uploads.current().entries.map((each) => each.asked)).toEqual([undefined, undefined]);

    let result: unknown = "pending";
    await act(async () => void uploads.current().stopOrCancel("stop", [a!.key, b!.key]).then((r) => (result = r)));
    expect(uploads.current().entries[1]?.asked).toBe("stop"); // "Will stop once sent."
    await act(async () => replies[0]?.({ document: doc("id-b"), duplicate: false, in_trash: false })); // b stored
    await act(async () => releaseA());

    expect(api.stopDocument.mock.calls.map(([id]) => id).sort()).toEqual(["id-a", "id-b"]);
    expect(result).toEqual({ count: 0, why: "" });
    expect(uploads.current().entries.map((each) => [uploadStateText(each.state), each.asked])).toEqual([
      ["Stopped", undefined],
      ["Stopped", undefined],
    ]);
    await uploads.unmount();
  });

  it("forgets an ask when the upload fails, so a Retry never promises a Stop that won't come", async () => {
    let fail: (error: unknown) => void = () => undefined;
    api.uploadDocument.mockImplementation(() => new Promise((_, reject) => (fail = reject)));
    const uploads = await mount();
    await act(async () => void uploads.current().addFiles([new File(["x"], "a.pdf")]));
    const [a] = uploads.current().entries;
    let result: unknown = "pending";
    await act(async () => void uploads.current().stopOrCancel("stop", [a!.key]).then((r) => (result = r)));
    expect(uploads.current().entries[0]?.asked).toBe("stop");
    await act(async () => fail(new TypeError("Failed to fetch"))); // not sent: nothing to stop
    expect(result).toEqual({ count: 0, why: "" });
    expect(uploads.current().entries[0]?.state.kind).toBe("error");
    expect(uploads.current().entries[0]?.asked).toBeUndefined(); // a Retry later shows no "Will stop once sent."
    await uploads.unmount();
  });

  it("a second Stop or Cancel of a file still being sent never hangs the first: both are done once it is stored", async () => {
    api.uploadDocument.mockImplementation(() => new Promise((resolve) => replies.push(resolve)));
    api.stopDocument.mockImplementation(async (id: string) => stoppedDoc(id));
    const uploads = await mount();
    await act(async () => void uploads.current().addFiles([new File(["x"], "a.pdf")]));
    const [a] = uploads.current().entries;
    const results: unknown[] = [];
    await act(async () => void uploads.current().stopOrCancel("stop", [a!.key]).then((r) => results.push(["stop", r])));
    await act(async () => void uploads.current().stopOrCancel("cancel", [a!.key]).then((r) => results.push(["cancel", r])));
    await act(async () => replies[0]?.({ document: doc("id-a"), duplicate: false, in_trash: false }));

    expect(api.stopDocument).toHaveBeenCalledWith("id-a");
    expect(api.deleteDocument).toHaveBeenCalledWith("id-a");
    expect(results).toEqual([
      ["stop", { count: 0, why: "" }],
      ["cancel", { count: 0, why: "" }],
    ]);
    await uploads.unmount();
  });

  it("counts a file still being sent that turned out a copy already in Kaagaz as not stopped", async () => {
    api.uploadDocument.mockImplementation(() => new Promise((resolve) => replies.push(resolve)));
    const uploads = await mount();
    await act(async () => void uploads.current().addFiles([new File(["x"], "a.pdf")]));
    const [a] = uploads.current().entries;
    let refused: unknown;
    let asking: Promise<unknown> = Promise.resolve();
    await act(async () => void (asking = uploads.current().stopOrCancel("cancel", [a!.key]).then((r) => (refused = r))));
    await act(async () => {
      replies[0]?.({ document: doc("old", { status: "ready" }), duplicate: true, in_trash: false });
      await asking;
    });
    expect(refused).toEqual({ count: 1, why: STOP_TEXT.notApplied });
    expect(api.deleteDocument).not.toHaveBeenCalled();
    await uploads.unmount();
  });

  it("Read again on a stopped row follows the file again; too soon after the last, it says when", async () => {
    const { uploads, keys } = await three();
    api.stopDocument.mockImplementation(async (id: string) => stoppedDoc(id));
    await act(async () => void (await uploads.current().stopOrCancel("stop", [keys[0]!])));
    api.reprocessDocument.mockResolvedValue(doc("id-a.pdf", { status: "received" }));
    let why: unknown = "unset";
    await act(async () => void (why = await uploads.current().readAgain(keys[0]!, "id-a.pdf")));
    expect(why).toBeNull();
    expect(uploads.current().entries[0]?.state.kind).toBe("reading");

    const words = "This document was sent to be read again less than 10 minutes ago. Please wait and try again.";
    api.reprocessDocument.mockRejectedValue(new FakeApiError(429, "rate_limited", words, { reason: "cooldown" }));
    await act(async () => void (why = await uploads.current().readAgain(keys[1]!, "id-b.pdf")));
    expect(why).toBe("You can read it again in 10 minutes.");
    const hourly = "Too many documents were sent to be read again in the last hour. Please try again later.";
    api.reprocessDocument.mockRejectedValue(new FakeApiError(429, "rate_limited", hourly, { reason: "hourly" }));
    await act(async () => void (why = await uploads.current().readAgain(keys[1]!, "id-b.pdf")));
    expect(why).toBe(hourly); // the hourly limit's words stay as today
    await uploads.unmount();
  });
});
