// Storage limit per organisation (#516): once a file of a drop is refused `storage_full`, the
// rest of that drop is not started (UploadArea.tsx useUploads); each says the API's words, as
// not sent, with no Retry. Another drop is its own. Run in node with React's own act() and a
// bare container, as tests/upload-follow.test.ts; the API is a fake.

import { act, createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UPLOADS_AT_ONCE } from "@/components/documents/uploadQueue";

const FULL = "This organisation has used its 50 GB of storage. Delete files forever from the Trash to free space, or ask Kaagaz to raise the limit.";

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
  return { ApiError, uploadDocument: vi.fn() };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  getAiSettings: () => new Promise(() => undefined),
  getBatch: () => new Promise(() => undefined),
  getUploadLimits: () => new Promise(() => undefined),
  isStorageFull: (caught: unknown) => caught instanceof api.ApiError && caught.code === "storage_full",
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

const files = (count: number) => Array.from({ length: count }, (_, n) => new File(["%PDF-1.7"], `f${n}.pdf`));

describe("a drop of files once one is refused because storage is full", () => {
  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const page = { visibilityState: "visible", activeElement: null, ...target() };
    vi.stubGlobal("document", page);
    vi.stubGlobal("window", { document: page, HTMLIFrameElement: class {}, ...target() });
    vi.stubGlobal("navigator", { onLine: true });
    api.uploadDocument.mockRejectedValue(new api.ApiError(413, "storage_full", FULL));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("starts no more of that drop: only the files already being sent were sent, and every row says why", async () => {
    const uploads = await mount();
    await act(() => uploads.current().addFiles(files(UPLOADS_AT_ONCE + 4)));
    expect(api.uploadDocument).toHaveBeenCalledTimes(UPLOADS_AT_ONCE);
    const { entries } = uploads.current();
    expect(entries).toHaveLength(UPLOADS_AT_ONCE + 4);
    for (const entry of entries) expect(entry.state).toEqual({ kind: "error", message: FULL, retry: false });
    await uploads.unmount();
  });

  it("does not stop another drop", async () => {
    const uploads = await mount();
    await act(() => uploads.current().addFiles(files(1)));
    await act(() => uploads.current().addFiles(files(1)));
    expect(api.uploadDocument).toHaveBeenCalledTimes(2);
    await uploads.unmount();
  });
});
