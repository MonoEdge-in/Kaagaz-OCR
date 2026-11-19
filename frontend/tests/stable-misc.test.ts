// Stability pass (Tushar, 7 Nov 2026), the smaller frontend fixes:
// - B4: a folder changed on the Documents screen reaches the shell's folder list (the tray, the
//   department new files go into), and a department picked that is gone is not used;
// - B5: an opened folder in Trash says single files can be restored from it;
// - B14: "for good" in the sentences about deleting for good;
// - B15: ids (a question's, a folder upload's) are made without crypto.randomUUID outside a secure context;
// - B17: the label descriptions never promise privacy (labels restrict nothing);
// - B19: the sign-in page's server-side calls send the visitor's IP (the backend's A7).

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Folder } from "@/lib/types";
import { install } from "./fake-dom";
import type { FakeDocument, FakeElement } from "./fake-dom";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");

const api = vi.hoisted(() => ({ listFolders: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...api,
  getAiSettings: () => new Promise(() => undefined),
  getUploadLimits: () => new Promise(() => undefined),
  listFolderUploads: async () => ({ items: [] }),
  getPreviousFolderUpload: async () => ({ previous: null }),
}));
// Changed on purpose (#697): the shell now asks canDeleteForever(me), which reads the session's scope.
vi.mock("@/lib/session", () => ({ useSession: () => ({ me: { user: { id: "u1", role: "admin" }, session: { scope: "admin" } } }) }));
vi.mock("@/components/documents/UploadArea", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/documents/UploadArea")>()),
  UploadStatus: () => null,
}));

describe("B4: folder changes reach the shell", () => {
  let doc: FakeDocument;
  let root: Root | null = null;

  beforeEach(() => {
    doc = install();
    vi.useFakeTimers();
    api.listFolders.mockResolvedValue({ items: [] });
  });

  afterEach(async () => {
    await act(async () => root?.unmount());
    root = null;
    vi.useRealTimers();
    vi.unstubAllGlobals();
    api.listFolders.mockReset();
  });

  it("asks for the folders again when a screen says it changed them", async () => {
    const { UploadsProvider, useSharedUploads } = await import("@/components/shell/Uploads");
    let shared: ReturnType<typeof useSharedUploads> | null = null;
    function Probe() {
      shared = useSharedUploads(() => undefined);
      return null;
    }
    const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
    const { createRoot } = await import("react-dom/client");
    root = createRoot(container as unknown as Element);
    await act(async () => root!.render(createElement(UploadsProvider, null, createElement(Probe))));
    await act(async () => void (await vi.advanceTimersByTimeAsync(0)));
    const before = api.listFolders.mock.calls.length;
    const changed = shared!.foldersChanged;
    await act(async () => shared!.foldersEdited());
    await act(async () => void (await vi.advanceTimersByTimeAsync(0)));
    expect(shared!.foldersChanged).toBe(changed + 1);
    expect(api.listFolders.mock.calls.length).toBe(before + 1);
  });

  it("tells the shell after a folder change, a folder in Trash, and a restore in Trash", () => {
    const view = read("components/documents/DocumentsView.tsx");
    const body = (name: string) => view.slice(view.indexOf(`function ${name}(`), view.indexOf("\n  }\n", view.indexOf(`function ${name}(`)));
    expect(body("afterFolderChange")).toContain("foldersEdited();");
    expect(body("afterTrashFolder")).toContain("foldersEdited();");
    expect(body("afterRemoved")).toContain("if (library.trash) foldersEdited();");
  });

  it("uses a department picked only while it is still one", async () => {
    const { chosenDepartment } = await import("@/components/files/departments");
    const tax = { id: "tax", name: "Tax", kind: "department", parent_id: null } as Folder;
    expect(chosenDepartment("tax", [tax])).toBe("tax");
    expect(chosenDepartment(null, [tax])).toBeNull(); // "no department" was picked
    expect(chosenDepartment("gone", [tax])).toBeNull(); // deleted since: none, then the last one used
    expect(chosenDepartment(undefined, [tax])).toBeNull(); // nothing picked, nothing kept
    expect(read("components/documents/DocumentsView.tsx")).toContain("const department = chosenDepartment(departmentChoice, departments);");
  });

  // PRD 9 F-01 (D1): the department last used in this browser is never sent in place of a pick.
  it("never sends the department last used when nothing is picked", async () => {
    vi.stubGlobal("localStorage", { getItem: () => "tax", setItem: () => undefined, removeItem: () => undefined });
    const { chosenDepartment } = await import("@/components/files/departments");
    const tax = { id: "tax", name: "Tax", kind: "department", parent_id: null } as Folder;
    expect(chosenDepartment(undefined, [tax])).toBeNull();
    expect(chosenDepartment("gone", [tax])).toBeNull();
    vi.unstubAllGlobals();
    for (const file of ["components/dashboard/Dashboard.tsx", "components/shell/Uploads.tsx", "components/documents/DocumentsView.tsx"]) {
      expect(read(file)).not.toContain("lastDepartment");
    }
  });
});

describe("B5: an opened folder in Trash", () => {
  it("says single files can be restored here, the whole folder from Trash", () => {
    const view = read("components/documents/DocumentsView.tsx");
    expect(view).toContain("Restore single files here, or the whole folder from Trash.");
    expect(view).not.toContain("so it can only be looked at");
  });
});

describe("B14: for good", () => {
  it("is in the sentences about deleting for good", async () => {
    const { allowForeverQuestion, describeAction, userStatus } = await import("@/components/admin/logic");
    expect(allowForeverQuestion("Ravi")).toMatch(/^Let Ravi delete documents in Trash for good\?/);
    expect(describeAction("folder.delete_forever")).toBe("Deleted a folder for good");
    expect(describeAction("user.delete_forever_on")).toBe("Allowed a user to delete for good");
    expect(describeAction("user.delete_forever_off")).toBe("Stopped a user deleting for good");
    const allowed = { disabled: false, locked_until: null, totp_enabled: false, must_change_password: false, can_delete_forever: true };
    expect(userStatus(allowed as never, 0).map((part) => part.text)).toContain("Can delete for good");
    const view = read("components/documents/DocumentsView.tsx");
    expect(view).toContain("To delete for good, <Link");
    expect(view).toContain("can delete documents for good.");
  });
});

describe("B15: an id made outside a secure context", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is a version 4 uuid from getRandomValues when randomUUID is missing", async () => {
    const { randomId } = await import("@/lib/api");
    vi.stubGlobal("crypto", { getRandomValues: globalThis.crypto.getRandomValues.bind(globalThis.crypto) });
    const ids = new Set(Array.from({ length: 50 }, () => randomId()));
    expect(ids.size).toBe(50);
    for (const id of ids) expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });

  it("is randomUUID's where there is one, and every caller uses it", async () => {
    const { randomId } = await import("@/lib/api");
    vi.stubGlobal("crypto", { randomUUID: () => "11111111-2222-4333-8444-555555555555" });
    expect(randomId()).toBe("11111111-2222-4333-8444-555555555555");
    for (const path of ["lib/api.ts", "components/documents/useFolderUploads.ts"]) {
      expect(read(path).match(/crypto\.randomUUID\(\)/g) ?? [], path).toHaveLength(path === "lib/api.ts" ? 1 : 0);
    }
  });
});

describe("B17: the label descriptions", () => {
  it("never promise privacy: everyone in the organisation can still open a labelled paper", async () => {
    const { LABEL_DESCRIPTIONS } = await import("@/components/files/labels");
    for (const label of ["confidential", "restricted"] as const) {
      expect(LABEL_DESCRIPTIONS[label]).toContain("Everyone in your organisation can still open it.");
      expect(LABEL_DESCRIPTIONS[label]).not.toMatch(/named few|only|who work on it/i);
    }
    expect(LABEL_DESCRIPTIONS.restricted).toBe(
      "A label for papers to handle with care, such as deeds and personal papers. Everyone in your organisation can still open it.",
    );
  });
});

describe("B19: the sign-in page's server-side calls", () => {
  it("send the visitor's IP from the incoming request as X-Forwarded-For", async () => {
    const { signInOptions, visitorIp } = await import("@/app/login/signInOptions");
    const sent: (string | null)[] = [];
    const get = (async (_url: string, init?: RequestInit) => {
      sent.push(new Headers(init?.headers).get("X-Forwarded-For"));
      return new Response(JSON.stringify({ providers: [] }), { status: 200 });
    }) as typeof fetch;
    await signInOptions(get, "203.0.113.7");
    expect(sent).toEqual(["203.0.113.7", "203.0.113.7"]);
    sent.length = 0;
    await signInOptions(get, null);
    expect(sent).toEqual([null, null]);
    // The first address of the incoming header (Caddy sets it), trimmed; none without one.
    expect(visitorIp(new Headers({ "x-forwarded-for": " 203.0.113.7, 10.0.0.2" }))).toBe("203.0.113.7");
    expect(visitorIp(new Headers())).toBeNull();
    const page = read("app/login/page.tsx");
    expect(page).toContain("await signInOptions(fetch, visitorIp(await headers()))");
  });
});

describe("the sign-in page's server-side calls, after the reviews", () => {
  afterEach(() => vi.restoreAllMocks());

  it("log a refusal or no answer, so Google sign-in missing from the page leaves a trace", async () => {
    const { signInOptions } = await import("@/app/login/signInOptions");
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const busy = (async () => new Response("{}", { status: 503 })) as typeof fetch;
    expect(await signInOptions(busy)).toEqual({ providers: [], emailOn: false });
    expect(warn.mock.calls.map((call) => String(call[0]))).toEqual(
      expect.arrayContaining([expect.stringContaining("/auth/session"), expect.stringContaining("503")]),
    );
    warn.mockClear();
    const off = (async (url: string) => new Response("{}", { status: url.endsWith("/auth/email") ? 404 : 200 })) as typeof fetch;
    await signInOptions(off);
    expect(warn).not.toHaveBeenCalled(); // email off (404) is an answer, not a problem
  });
});
