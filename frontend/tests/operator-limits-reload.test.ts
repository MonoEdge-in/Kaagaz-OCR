// The operator's storage limit (#516) and folder upload limits (#484): when the load after a save
// fails, the screen says so, with Try again, instead of the value from before the save, which may
// no longer be the one in force. The hooks run in node with React's own act() and a bare
// container, as tests/upload-follow.test.ts; the API is a fake.

import { act, createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FolderLimits, StorageLimit } from "@/lib/types";

const api = vi.hoisted(() => ({
  getStorageLimit: vi.fn(),
  getFolderUploadLimits: vi.fn(),
  getOrgFolderUploadLimits: vi.fn(),
}));

vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), ...api }));

const STORAGE: StorageLimit = { storage_gb: 80, platform_gb: null, effective_gb: 80, default_gb: 50, most_gb: 10000, used_bytes: 0 };
const LIMITS: FolderLimits = { max_files: 100, max_gb: 2, max_folders: 10 };
const DEFAULTS = { max_files: 50000, max_gb: 5, max_folders: 10000 };

type Loaded = { value: unknown; failed: string | null; reload(): void };

async function mount(useHook: () => Loaded) {
  const { createRoot } = await import("react-dom/client");
  let latest: Loaded | null = null;
  function Probe() {
    latest = useHook();
    return null;
  }
  const container = { nodeType: 1, tagName: "DIV", namespaceURI: null, textContent: "", ownerDocument: null, addEventListener: () => undefined, removeEventListener: () => undefined };
  const root = createRoot(container as unknown as Element);
  await act(async () => root.render(createElement(Probe)));
  return {
    current: () => {
      if (latest === null) throw new Error("not rendered");
      return latest;
    },
    reload: () => act(async () => latest?.reload()),
    unmount: () => act(() => root.unmount()),
  };
}

/** Each load answers `ok`, but the second (the one after a save) fails. */
function secondFails(load: ReturnType<typeof vi.fn>, ok: unknown) {
  load.mockReset().mockResolvedValueOnce(ok).mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValue(ok);
}

describe("a reload that fails after a save", () => {
  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const target = { addEventListener: () => undefined, removeEventListener: () => undefined };
    const page = { visibilityState: "visible", activeElement: null, ...target };
    vi.stubGlobal("document", page);
    vi.stubGlobal("window", { document: page, HTMLIFrameElement: class {}, ...target });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetAllMocks();
  });

  const hooks = async () => {
    const storage = await import("@/components/operator/StorageLimit");
    const folder = await import("@/components/operator/FolderUploadLimits");
    return [
      ["the platform's storage limit", () => storage.useStorageLimit()],
      ["an organisation's storage limit", () => storage.useStorageLimit("o1")],
      ["the platform's folder upload limits", () => folder.useLimits()],
      ["an organisation's folder upload limits", () => folder.useLimits("o1")],
    ] as const;
  };

  it("shows that it failed, not the value from before, then the new value after Try again", async () => {
    for (const [what, useHook] of await hooks()) {
      secondFails(api.getStorageLimit, STORAGE);
      secondFails(api.getOrgFolderUploadLimits, { own: LIMITS, platform: LIMITS, defaults: DEFAULTS });
      secondFails(api.getFolderUploadLimits, { ...LIMITS, defaults: DEFAULTS });
      const shown = await mount(useHook);
      expect(shown.current().value, what).not.toBeNull();
      await shown.reload(); // after a save
      expect(shown.current().failed, what).not.toBeNull();
      expect(shown.current().value, what).toBeNull();
      await shown.reload(); // Try again
      expect(shown.current().failed, what).toBeNull();
      expect(shown.current().value, what).not.toBeNull();
      await shown.unmount();
    }
  });
});
