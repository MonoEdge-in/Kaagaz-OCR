// #697 (night meeting, 19 Nov 2026): an upload whose read copy is in Trash offers Restore from Trash
// (primary) and, to who may delete forever while this tab holds the file, Upload as new file after
// one question (no password); everyone else is told to ask an admin. Restore brings the copy into
// the folder the upload was going to, or says it stayed where it was and why. The hook runs in node
// with React's own act() and a bare container, as tests/upload-drop-storage-full.test.ts.

import { act, createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BatchTray, ReplaceDialog, restoredText } from "@/components/documents/UploadArea";
import { buildTree } from "@/components/documents/folderTree";
import type { Folder } from "@/lib/types";
import type { UploadEntry } from "@/components/documents/uploadQueue";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    constructor(
      public status: number,
      public code: string,
      message: string,
    ) {
      super(message);
    }
  }
  return { ApiError, uploadDocument: vi.fn(), restoreDocument: vi.fn(), moveDocument: vi.fn() };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  getAiSettings: () => new Promise(() => undefined),
  getBatch: () => new Promise(() => undefined),
  getUploadLimits: () => new Promise(() => undefined),
  isStorageFull: () => false,
  moveDocument: api.moveDocument,
  restoreDocument: api.restoreDocument,
  uploadDocument: api.uploadDocument,
  uploadVersion: vi.fn(),
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ me: { user: { id: "u1" } } }) }));
vi.mock("@/components/documents/pageCount", () => ({ checkPages: async () => ({ pages: null, problem: null }) }));

const invoices = { id: "f1", name: "Invoices", parent_id: null, created_at: "2026-01-01T00:00:00Z" } as Folder;
const TRAY = {
  views: {},
  stored: [],
  onClearFinished: () => undefined,
  onRestore: () => undefined,
  onRetry: () => undefined,
  tree: buildTree([invoices]),
  onUndone: () => undefined,
  canReread: true,
};
const inTrash = (asNew?: boolean): UploadEntry => ({
  key: "upload-1",
  name: "bill.pdf",
  size: 10,
  batchId: "b1",
  asNew,
  state: { kind: "duplicate", documentId: "d1", existingName: "bill.pdf", inTrash: true },
});
const tray = (entry: UploadEntry, onReplace?: (key: string) => void) =>
  renderToStaticMarkup(createElement(BatchTray, { ...TRAY, entries: [entry], onReplace }));

describe("the tray row of a file whose copy is in Trash", () => {
  it("offers Restore from Trash first, and Upload as new file to who may delete forever", () => {
    const html = tray(inTrash(true), () => undefined);
    expect(html).toContain("This file is already in Trash as “bill.pdf”.");
    expect(html).toMatch(/class="btn btn-primary[^"]*"[^>]*>(<[^>]+>)*Restore from Trash/);
    expect(html.indexOf("Restore from Trash")).toBeLessThan(html.indexOf("Upload as new file"));
    expect(html).not.toContain("Ask an admin");
  });

  it("tells everyone else to ask an admin, with Restore only", () => {
    const html = tray(inTrash(true));
    expect(html).toContain("Restore from Trash");
    expect(html).toContain("Ask an admin to upload it as a new file.");
    expect(html).not.toContain("Upload as new file");
  });

  it("offers only Restore once the tab no longer holds the file (after a reload)", () => {
    const html = tray(inTrash(), () => undefined);
    expect(html).toContain("Restore from Trash");
    expect(html).not.toContain("Upload as new file");
    expect(html).not.toContain("Ask an admin");
  });
});

describe("the question before Upload as new file", () => {
  it("asks once, with no password, Cancel first and focused, Replace as a danger", () => {
    const html = renderToStaticMarkup(createElement(ReplaceDialog, { onCancel: () => undefined, onReplace: () => undefined }));
    expect(html).toContain("Replace the copy in Trash?");
    expect(html).toContain("The old copy&#x27;s fixed words and history are deleted. This can&#x27;t be undone.");
    expect(html).toMatch(/<button[^>]*class="btn"[^>]*data-autofocus[^>]*>Cancel<\/button>/);
    expect(html).toMatch(/<button[^>]*class="btn btn-danger"[^>]*>Replace<\/button>/);
    expect(html.indexOf(">Cancel<")).toBeLessThan(html.indexOf(">Replace<"));
    expect(html).not.toContain("password");
  });
});

describe("what Restore says it did", () => {
  const restored = { kind: "restored" as const, documentId: "d1", existingName: "bill.pdf" };
  it("names the folder it went into, or where it stayed and why", () => {
    const tree = buildTree([invoices]);
    expect(restoredText({ ...restored, into: "f1" }, tree)).toBe("Restored into “Invoices”.");
    expect(restoredText({ ...restored, notMoved: "Its folder was deleted." }, tree)).toBe(
      "Restored to where it was. Its folder was deleted.",
    );
    expect(restoredText(restored, tree)).toBe("Restored “bill.pdf” from Trash.");
  });
});

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

const document = { id: "d1", display_name: "bill.pdf", status: "ready", folder_id: null };

describe("Restore and Upload as new file in the upload queue", () => {
  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const page = { visibilityState: "visible", activeElement: null, ...target() };
    vi.stubGlobal("document", page);
    vi.stubGlobal("window", { document: page, HTMLIFrameElement: class {}, ...target() });
    vi.stubGlobal("navigator", { onLine: true });
    api.uploadDocument.mockResolvedValue({ document, duplicate: true, in_trash: true });
    api.restoreDocument.mockResolvedValue(document);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  async function droppedInto(folderId: string) {
    const uploads = await mount();
    await act(() => uploads.current().addFiles([new File(["%PDF-1.7"], "bill.pdf")], folderId));
    const [entry] = uploads.current().entries;
    expect(entry?.state).toMatchObject({ kind: "duplicate", inTrash: true });
    return { uploads, key: entry?.key ?? "" };
  }

  it("restores the copy, then moves it into the folder the upload was going to", async () => {
    api.moveDocument.mockResolvedValue(document);
    const { uploads, key } = await droppedInto("f1");
    await act(() => uploads.current().restore(key, "d1", "bill.pdf"));
    expect(api.moveDocument).toHaveBeenCalledWith("d1", "f1");
    expect(uploads.current().entries[0]?.state).toEqual({
      kind: "restored", documentId: "d1", existingName: "bill.pdf", into: "f1", notMoved: undefined,
    });
    await uploads.unmount();
  });

  it("says it stayed where it was, and why, when the move is refused", async () => {
    api.moveDocument.mockRejectedValue(new api.ApiError(409, "folder_gone", "Its folder was deleted."));
    const { uploads, key } = await droppedInto("f1");
    await act(() => uploads.current().restore(key, "d1", "bill.pdf"));
    expect(uploads.current().entries[0]?.state).toMatchObject({ kind: "restored", into: undefined, notMoved: "Its folder was deleted." });
    await uploads.unmount();
  });

  it("sends the same file again, into the same folder and drop, asking the server to replace the copy", async () => {
    const { uploads, key } = await droppedInto("f1");
    api.uploadDocument.mockResolvedValue({ document: { ...document, id: "d2" }, duplicate: false, in_trash: false });
    await act(() => uploads.current().replace(key));
    const [first, again] = api.uploadDocument.mock.calls;
    expect(api.uploadDocument).toHaveBeenCalledTimes(2);
    expect(first?.[9]).toBeUndefined();
    expect(again?.[0]).toBe(first?.[0]); // the same file
    expect([again?.[3], again?.[5], again?.[9]]).toEqual(["f1", first?.[5], true]);
    expect(uploads.current().entries[0]?.state).toMatchObject({ documentId: "d2" });
    await uploads.unmount();
  });
});
