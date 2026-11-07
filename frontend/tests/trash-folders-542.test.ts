// #542 (Tushar, 7 Nov 2026): Trash keeps a deleted folder's structure. A folder is one row of
// Trash (its icon, name, where it was, what it holds, when and by whom), it opens read only under
// "Trash › Accounts › 2025", Restore says where it went (or why not where it was), Delete forever
// asks with the folder's name and file count (and the password for whoever types one, #538), and
// a selection may hold folders and documents together. The words are pure functions; the list is
// rendered with react-dom in the small fake DOM (tests/fake-dom.ts) and pressed.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentsTable } from "@/components/documents/DocumentsTable";
import { TrashCrumbs } from "@/components/documents/FolderNav";
import { buildTree } from "@/components/documents/folderTree";
import {
  bulkDoneMessage,
  bulkForeverQuestion,
  folderForeverQuestion,
  folderRestoredMessage,
  heldText,
  itemsCount,
  trashEmpty,
  wasInText,
} from "@/components/documents/trash";
import type { DocumentSummary, Folder, Me, TrashedFolder } from "@/lib/types";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

vi.mock("@/lib/session", () => ({ useSession: () => ({ refresh: () => undefined }) }));
vi.mock("next/link", async () => {
  const { createElement: make } = await import("react");
  return { default: ({ href, children }: { href: string; children: unknown }) => make("a", { href }, children as never) };
});

const year: TrashedFolder = {
  id: "f1",
  name: "2025",
  kind: "year",
  was_in: ["Accounts"],
  files: 4,
  folders: 3,
  deleted_at: "2026-11-06T10:00:00Z",
  deleted_by_name: "Asha",
  can_restore: true,
};
const restored = (more: { name?: string; location?: string[]; renamed?: boolean; relocated?: boolean } = {}) => ({
  folder: { id: "f1", name: more.name ?? "2025", parent_id: null, created_at: "2026-01-01T00:00:00Z" } as Folder,
  location: more.location ?? ["Accounts"],
  renamed: more.renamed ?? false,
  relocated: more.relocated ?? false,
  files: 4,
  folders: 3,
});

describe("the words for a folder in Trash", () => {
  it("says where it was, the top level, or that its folder is gone", () => {
    expect(wasInText(["Accounts", "2025"])).toBe("Was in: Accounts › 2025");
    expect(wasInText([])).toBe("Was in: Top level");
    expect(wasInText(null)).toBe("Was in: a folder that no longer exists");
  });

  it("counts what it holds", () => {
    expect(heldText(year)).toBe("4 files, 3 folders");
    expect(heldText({ files: 1, folders: 0 })).toBe("1 file, 0 folders");
  });

  it("says where a restored folder went, or why it is not where it was", () => {
    expect(folderRestoredMessage(restored())).toBe("Folder restored to Accounts.");
    expect(folderRestoredMessage(restored({ location: [] }))).toBe("Folder restored to Top level.");
    expect(folderRestoredMessage(restored({ relocated: true, location: [] }))).toBe(
      "Restored to Top level, because its folder no longer exists.",
    );
    expect(folderRestoredMessage(restored({ renamed: true, name: "2025 (2)" }))).toBe(
      "Restored as “2025 (2)”, because a folder with that name already exists.",
    );
  });

  it("asks before deleting forever with the name and the files", () => {
    expect(folderForeverQuestion(year)).toBe("Delete “2025” and its 4 files forever? This can't be undone.");
    expect(folderForeverQuestion({ name: "Old", files: 1 })).toBe("Delete “Old” and its 1 file forever? This can't be undone.");
  });

  it("counts a selection of documents and folders", () => {
    expect(itemsCount(3)).toBe("3 documents");
    expect(itemsCount(0, 1)).toBe("1 folder");
    expect(itemsCount(2, 1)).toBe("2 documents and 1 folder");
    expect(bulkDoneMessage("restore", 1, 2)).toBe("Restored 1 document and 2 folders.");
    expect(bulkForeverQuestion(1, 1)).toBe(
      "Delete 1 document and 1 folder for good, with every file and version in them? They can't be brought back.",
    );
    expect(bulkForeverQuestion(2)).toBe(
      "Delete 2 selected documents for good, with all their versions? They can't be brought back.",
    );
  });

  it("has an empty state for Trash and for an open folder with nothing in it", () => {
    expect(trashEmpty(false).title).toBe("Trash is empty");
    expect(trashEmpty(true)).toEqual({
      title: "This folder is empty",
      text: "Nothing was in it when it was deleted, or what was is back in Documents.",
    });
    const view = readFileSync(join(__dirname, "../components/documents/DocumentsView.tsx"), "utf8");
    expect(view).toContain("trashEmpty(opened !== null)"); // the screen says it
    expect(view).toContain("list.total + folderRows.length"); // a Trash of folders only is not empty
  });

  it("says option 2 of a folder's Delete in #542's words", () => {
    const dialog = readFileSync(join(__dirname, "../components/documents/DeleteFolderDialog.tsx"), "utf8");
    expect(dialog).toContain("Everything goes to Trash. You can restore the {noun} for {RESTORE_DAYS} days.");
  });
});

// --- The Trash list, rendered and pressed ---

const admin = (totp_enabled: boolean): Me => ({
  user: {
    id: "u1",
    email: "asha@example.in",
    display_name: "Asha",
    role: "admin",
    must_change_password: false,
    totp_enabled,
    two_step_turned_off: !totp_enabled,
    can_delete_forever: false,
    email_confirmed: true,
  },
  customer: { id: "c1", name: "Sharma Traders" },
  session: { scope: "admin" },
  providers: [],
});
const binned = {
  id: "d1",
  display_name: "Bill.pdf",
  detected_type: "pdf",
  size_bytes: 1000,
  status: "ready",
  failure_code: null,
  status_message: "Ready.",
  page_count: 1,
  created_at: "2026-10-15T10:00:00Z",
  uploaded_by_name: "Priya",
  folder_id: null,
  tags: [],
  deleted_at: "2026-11-06T10:00:00Z",
  version_no: 1,
  family_id: "d1",
  can_delete: true,
  attempts: 1,
  max_attempts: 5,
  newer_version: null,
  progress: null,
  time_left: null,
  doc_type: null,
  languages: [],
  pages_unreadable: 0,
  pages_uncertain: 0,
  values_to_review: 0,
} as unknown as DocumentSummary;

let doc: FakeDocument;
let root: Root | null = null;
let calls: string[] = [];
let removed: string[] = [];
let done: string[] = [];
let opened: string[] = [];

function serve(routes: Record<string, [number, unknown?][]>) {
  calls = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const call = `${init.method ?? "GET"} ${url}`;
    calls.push(call);
    const [status, body] = routes[call]?.shift() ?? [500, { error: { code: "error", message: `no route ${call}` } }];
    return new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
}

async function show(me: Me, folders: TrashedFolder[], items: DocumentSummary[] = [], actions = true) {
  removed = [];
  done = [];
  opened = [];
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  const props = {
    items,
    inTrash: true,
    me,
    now: Date.parse("2026-11-07T10:00:00Z"),
    tree: buildTree([]),
    tags: [],
    suggestions: [],
    movedLeaves: false,
    onRemoved: (message: string) => removed.push(message),
    onSaved: () => undefined,
    onFocusList: () => undefined,
    onTagCreated: () => undefined,
    onFolderCreated: () => undefined,
    onUploadVersion: () => undefined,
    trashFolders: folders,
    onOpenFolder: (folder: TrashedFolder) => opened.push(folder.id),
    onFolderDone: actions ? (message: string) => done.push(message) : undefined,
  } as unknown as Parameters<typeof DocumentsTable>[0];
  await act(async () => root!.render(createElement(DocumentsTable, props)));
  return container;
}

const all = () => doc.body.all();
const byLabel = (label: string) => all().find((element) => element.getAttribute("aria-label") === label) ?? null;
const button = (words: string) =>
  all().find(
    (element) =>
      element.tagName === "BUTTON" &&
      (element.textContent.trim() === words ||
        element.all().some((inner) => inner.tagName === "SPAN" && inner.textContent === words)),
  ) ?? null;
const byId = (id: string) => all().find((element) => element.getAttribute("id") === id) ?? null;
const press = (element: FakeElement | null) => act(async () => void fire(element!, "click", { button: 0 }));
async function type(box: FakeElement | null, text: string) {
  box!.value = text;
  await act(async () => void fire(box!, "input"));
}
async function tick(box: FakeElement | null) {
  box!.checked = !box!.checked;
  await act(async () => void fire(box!, "click", { button: 0 }));
}

beforeEach(() => {
  doc = install();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  vi.unstubAllGlobals();
});

describe("a folder in the Trash list", () => {
  it("is one row with its name, where it was, what it holds and who deleted it, next to the files", async () => {
    serve({});
    await show(admin(true), [year], [binned]);
    const row = all().find((element) => element.getAttribute("data-id") === "folder:f1");
    expect(row?.textContent).toContain("2025");
    expect(row?.textContent).toContain("Was in: Accounts");
    expect(row?.textContent).toContain("4 files, 3 folders");
    expect(row?.textContent).toContain("Asha");
    expect(all().some((element) => element.getAttribute("data-id") === "d1")).toBe(true);
    expect(byLabel("Select folder 2025")).not.toBeNull();
  });

  it("opens when its name is pressed or its row clicked", async () => {
    serve({});
    Object.assign(window, { getSelection: () => null }); // the fake DOM selects no text
    await show(admin(true), [year]);
    await press(button("Open folder 2025"));
    await press(all().find((element) => element.getAttribute("data-id") === "folder:f1") ?? null);
    expect(opened).toEqual(["f1", "f1"]);
  });

  it("restores and says where it went, with each of the three toasts", async () => {
    serve({
      "POST /api/documents/trash/folders/f1/restore": [[200, restored()]],
      "POST /api/documents/trash/folders/f2/restore": [[200, restored({ relocated: true, location: [] })]],
      "POST /api/documents/trash/folders/f3/restore": [[200, restored({ renamed: true, name: "2025 (2)" })]],
    });
    const folders = ["f1", "f2", "f3"].map((id) => ({ ...year, id, name: `Year ${id}` }));
    await show(admin(true), folders);
    for (const folder of folders) await press(byLabel(`Restore folder ${folder.name}`));
    expect(done).toEqual([
      "Folder restored to Accounts.",
      "Restored to Top level, because its folder no longer exists.",
      "Restored as “2025 (2)”, because a folder with that name already exists.",
    ]);
  });

  it("deletes forever after the question, with the password for an admin without two-step", async () => {
    serve({ "POST /api/auth/confirm-password": [[204]], "DELETE /api/documents/trash/folders/f1/permanent": [[204]] });
    await show(admin(false), [year]);
    await press(byLabel("Delete folder 2025 forever"));
    const dialog = all().find((element) => element.tagName === "DIALOG");
    expect(dialog?.textContent).toContain("Delete “2025” and its 4 files forever? This can't be undone.");
    const box = byId("forever-folder-password-f1");
    expect(box).not.toBeNull();
    await type(box, "secret");
    const inDialog = dialog?.all().filter((element) => element.tagName === "BUTTON") ?? [];
    await press(inDialog.find((element) => element.textContent.startsWith("Delete forever")) ?? null);
    expect(calls).toEqual(["POST /api/auth/confirm-password", "DELETE /api/documents/trash/folders/f1/permanent"]);
    expect(done).toEqual(["Deleted 2025 for good."]);
  });

  it("is ticked with documents: one selection restores or deletes both", async () => {
    serve({
      "POST /api/documents/trash/folders/f1/restore": [[200, restored()]],
      "POST /api/documents/d1/restore": [[200, binned]],
      "DELETE /api/documents/trash/folders/f1/permanent": [[204]],
      "DELETE /api/documents/d1/permanent": [[204]],
    });
    await show(admin(true), [year], [binned]);
    await tick(byLabel("Select all visible documents"));
    await press(button("Restore (2)"));
    expect(calls).toEqual(["POST /api/documents/trash/folders/f1/restore", "POST /api/documents/d1/restore"]);
    expect(removed).toEqual(["Restored 1 document and 1 folder."]);

    await tick(byLabel("Select folder 2025"));
    await tick(byLabel("Select Bill.pdf"));
    await press(button("Delete forever (2)"));
    expect(doc.body.textContent).toContain("Delete 1 document and 1 folder for good");
    await press(button("Delete now"));
    expect(calls.slice(2)).toEqual(["DELETE /api/documents/trash/folders/f1/permanent", "DELETE /api/documents/d1/permanent"]);
    expect(removed[1]).toBe("Deleted 1 document and 1 folder for good.");
  });

  it("inside an open folder, its folders only open: no Restore, no Delete forever, no tick", async () => {
    serve({});
    await show(admin(true), [year], [], false);
    expect(byLabel("Restore folder 2025")).toBeNull();
    expect(byLabel("Delete folder 2025 forever")).toBeNull();
    expect(byLabel("Select folder 2025")).toBeNull();
    await press(button("Open folder 2025"));
    expect(opened).toEqual(["f1"]);
  });
});

describe("the path above an open folder in Trash", () => {
  it("is Trash › Accounts › 2025, each opening its folder, the last the current page", async () => {
    const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
    const { createRoot } = await import("react-dom/client");
    root = createRoot(container as unknown as Element);
    const went: (string | null)[] = [];
    const path = [
      { id: "a", name: "Accounts" },
      { id: "f1", name: "2025" },
    ];
    await act(async () => root!.render(createElement(TrashCrumbs, { path, onOpen: (id) => went.push(id) })));
    expect(byLabel("Folder path in Trash")?.textContent).toBe("TrashAccounts2025");
    expect(all().find((element) => element.getAttribute("aria-current") === "page")?.textContent).toBe("2025");
    await press(button("Trash"));
    await press(button("Accounts"));
    expect(went).toEqual([null, "a"]);
  });
});
