// PRD 9 F-04: a pick of files leaves out system files (.DS_Store, Thumbs.db, desktop.ini: folderPick's
// isSkipped) without a word, as a folder pick does; a row not uploaded (a .docm Kaagaz can't take)
// can be removed on its own. Run in node with React's own act() and a bare container, as
// tests/upload-drop-storage-full.test.ts; the API is a fake.

import { act, createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ uploadDocument: vi.fn() }));

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  getAiSettings: () => new Promise(() => undefined),
  getBatch: () => new Promise(() => undefined),
  getUploadLimits: () => new Promise(() => undefined),
  isStorageFull: () => false,
  restoreDocument: vi.fn(),
  uploadDocument: api.uploadDocument,
  uploadVersion: vi.fn(),
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ me: { user: { id: "u1" } } }) }));
vi.mock("@/components/documents/pageCount", () => ({ checkPages: async () => ({ pages: null, problem: null }) }));

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

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const page = { visibilityState: "visible", activeElement: null, ...target() };
  vi.stubGlobal("document", page);
  vi.stubGlobal("window", { document: page, HTMLIFrameElement: class {}, ...target() });
  vi.stubGlobal("navigator", { onLine: true });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("a pick of files with system files and one Kaagaz can't take", () => {
  it("never shows the system files, and the refused row can be removed", async () => {
    const uploads = await mount();
    const picked = [".DS_Store", "Thumbs.db", "desktop.ini", "macro.docm"].map((name) => new File(["x"], name));
    await act(() => uploads.current().addFiles(picked));

    const [refused, ...more] = uploads.current().entries;
    expect([refused?.name, refused?.state.kind, more]).toEqual(["macro.docm", "error", []]);
    expect(api.uploadDocument).not.toHaveBeenCalled();

    await act(async () => uploads.current().remove(refused?.key ?? ""));
    expect(uploads.current().entries).toEqual([]);
    await uploads.unmount();
  });
});
