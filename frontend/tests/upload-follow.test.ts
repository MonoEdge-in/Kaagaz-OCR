// The upload hook (UploadArea.tsx useUploads) follows each stored file through
// GET /api/batches/{id} until it is ready (PRD 5 U10). Run in node with React's
// own act() and a bare container: the hook renders nothing, so no DOM is needed
// beyond the event targets usePoll listens on. The API is a fake.

import { act, createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BatchView, DocumentSummary, UploadResult } from "@/lib/types";

const BATCH_DOCUMENT: DocumentSummary = {
  id: "d1",
  display_name: "Invoice.jpg",
  detected_type: "jpeg",
  size_bytes: 3,
  status: "received",
  failure_code: null,
  status_message: "Waiting to be read.",
  page_count: 1,
  created_at: "2026-10-09T10:00:00Z",
  uploaded_by_name: null,
  folder_id: null,
  tags: [],
  deleted_at: null,
  version_no: 1,
  family_id: "d1",
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
};

const api = vi.hoisted(() => ({
  getBatch: vi.fn(),
  uploadDocument: vi.fn(),
  uploadVersion: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
  getAiSettings: () => new Promise(() => undefined), // AI settings unknown: nothing capped
  getBatch: api.getBatch,
  getUploadLimits: () => new Promise(() => undefined), // the platform's limits apply
  isStorageFull: () => false, // no upload here is refused for storage (#516)
  restoreDocument: vi.fn(),
  uploadDocument: api.uploadDocument,
  uploadVersion: api.uploadVersion,
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ me: { user: { id: "u1" } } }) }));
vi.mock("@/components/documents/pageCount", () => ({ checkPages: async () => ({ pages: null, problem: null }) }));

type Uploads = ReturnType<typeof import("@/components/documents/UploadArea").useUploads>;

const target = () => ({ addEventListener: () => undefined, removeEventListener: () => undefined });

/** Mount useUploads in a bare root; the latest render's result is in current(). */
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

const ready: DocumentSummary = { ...BATCH_DOCUMENT, status: "ready", status_message: "Ready. You can ask about it." };

describe("following an upload until it is ready", () => {
  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.stubGlobal("navigator", { onLine: true });
    const page = { visibilityState: "visible", activeElement: null, ...target() };
    vi.stubGlobal("document", page);
    vi.stubGlobal("window", { document: page, HTMLIFrameElement: class {}, ...target() });
    vi.stubGlobal("navigator", { onLine: true });
    api.getBatch.mockImplementation(
      async (id: string): Promise<BatchView> => ({
        id,
        documents: [ready],
        counts: { total: 1, ready: 1, reading: 0, waiting: 0, attention: 0, failed: 0 },
        pages_read: 1,
        pages_total: 1,
        time_left: null,
      }),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("a single-file drop ends at Ready, with nothing else being read", async () => {
    const stored: UploadResult = { document: BATCH_DOCUMENT, duplicate: false, in_trash: false };
    api.uploadDocument.mockResolvedValue(stored);
    const uploads = await mount();
    await act(() => uploads.current().addFiles([new File(["abc"], "Invoice.jpg")]));
    expect(api.getBatch).toHaveBeenCalledTimes(1);
    const [entry] = uploads.current().entries;
    expect(entry?.state).toMatchObject({ kind: "done", document: { status: "ready" } });
    await uploads.unmount();
  });

  it("a new version sent again after a failure ends at the reply, as it has no batch", async () => {
    const stored: UploadResult = { document: BATCH_DOCUMENT, duplicate: false, in_trash: false };
    api.uploadVersion.mockRejectedValueOnce(new TypeError("network")).mockResolvedValueOnce(stored);
    const uploads = await mount();
    await act(() => uploads.current().addVersion(new File(["abc"], "Invoice.jpg"), { id: "d0", display_name: "Invoice" }));
    expect(uploads.current().entries[0]?.state).toMatchObject({ kind: "error", retry: true });
    const key = uploads.current().entries[0]?.key ?? "";
    await act(() => uploads.current().retry(key));
    expect(uploads.current().entries[0]?.state).toEqual({ kind: "done", documentId: "d1" });
    expect(api.getBatch).not.toHaveBeenCalled();
    await uploads.unmount();
  });
});
