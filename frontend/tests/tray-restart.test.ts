// Production QA (5 Nov 2026): through a server restart the tray (UploadArea.tsx useUploads) gave
// up on a batch after ten failed rounds and never asked again, so a stored file stayed
// "reading" for ever. It must keep asking, say that it can't check meanwhile, then show Ready.
// The hook runs in node with React's act(), as in tests/upload-follow.test.ts; the API is a fake.

import { act, createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BatchView, DocumentSummary, UploadResult } from "@/lib/types";

const RECEIVED: DocumentSummary = {
  id: "d1",
  display_name: "Invoice.jpg",
  detected_type: "jpeg",
  size_bytes: 3,
  status: "received",
  failure_code: null,
  status_message: "Waiting to be read.",
  page_count: 1,
  created_at: "2026-11-05T10:00:00Z",
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

const api = vi.hoisted(() => ({ getBatch: vi.fn(), uploadDocument: vi.fn() }));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
  getAiSettings: () => new Promise(() => undefined),
  getBatch: api.getBatch,
  getUploadLimits: () => new Promise(() => undefined),
  isStorageFull: () => false, // no upload here is refused for storage (#516)
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

const wait = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));

describe("the tray through a server restart", () => {
  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const page = { visibilityState: "visible", activeElement: null, ...target() };
    vi.stubGlobal("document", page);
    vi.stubGlobal("window", { document: page, HTMLIFrameElement: class {}, ...target() });
    vi.stubGlobal("navigator", { onLine: true });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("keeps checking a stored file after a minute of failures, says so, then shows Ready", async () => {
    let down = true;
    api.getBatch.mockImplementation(async (id: string): Promise<BatchView> => {
      if (down) throw new Error("502 Bad Gateway");
      const ready: DocumentSummary = { ...RECEIVED, status: "ready", status_message: "Ready. You can ask about it." };
      return {
        id,
        documents: [ready],
        counts: { total: 1, ready: 1, reading: 0, waiting: 0, attention: 0, failed: 0 },
        pages_read: 1,
        pages_total: 1,
        time_left: null,
      };
    });
    const stored: UploadResult = { document: RECEIVED, duplicate: false, in_trash: false };
    api.uploadDocument.mockResolvedValue(stored);
    const uploads = await mount();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    await act(() => uploads.current().addFiles([new File(["abc"], "Invoice.jpg")]));
    expect(uploads.current().entries[0]?.state.kind).toBe("reading");

    await wait(60_000); // the server is down for a minute
    expect(uploads.current().checkFailing).toBe(true);
    const during = api.getBatch.mock.calls.length;

    down = false;
    await wait(60_000);
    expect(api.getBatch.mock.calls.length).toBeGreaterThan(during);
    expect(uploads.current().entries[0]?.state).toMatchObject({ kind: "done", document: { status: "ready" } });
    expect(uploads.current().checkFailing).toBe(false);
    await uploads.unmount();
  });
});
