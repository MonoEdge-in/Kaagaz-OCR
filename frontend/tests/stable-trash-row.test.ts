// Stability pass (Tushar, 7 Nov 2026): a folder in Trash looks and works like a document in
// Trash. Tushar's screenshot of the test server showed the folder row's Restore and Delete
// forever spilling past the table's card, "Was in" clipped to "as in", the counts in the
// Status column and "now" in Trashed (B16). The row now has the document row's cells in the
// same order, its actions behind the same ⋯ menu inside the actions cell, the counts beside
// where it was, and the same time words. Delete forever keeps its question open after a wrong
// password (B6), closes once it worked (B11), and repeats while the server says 202 {remaining}
// (B18, the backend's A12), saying "Deleting… {n} left". Rendered with react-dom in the small
// fake DOM (tests/fake-dom.ts) and pressed.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentsTable } from "@/components/documents/DocumentsTable";
import { buildTree } from "@/components/documents/folderTree";
import { shortWhen } from "@/components/documents/logic";
import { folderForeverQuestion, heldText } from "@/components/documents/trash";
import type { DocumentSummary, Me, TrashedFolder } from "@/lib/types";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

vi.mock("@/lib/session", () => ({ useSession: () => ({ refresh: () => undefined }) }));
vi.mock("next/link", async () => {
  const { createElement: make } = await import("react");
  return {
    default: ({ href, children, className }: { href: string; children: unknown; className?: string }) =>
      make("a", { href, className }, children as never),
  };
});

const NOW = Date.parse("2026-11-07T10:00:00Z");
const round2: TrashedFolder = {
  id: "f1",
  name: "Round 2",
  kind: "custom",
  was_in: [],
  files: 9,
  folders: 26,
  deleted_at: "2026-11-07T10:00:00Z",
  deleted_by_name: "Tushar",
  can_restore: true,
};
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
  deleted_at: "2026-11-07T10:00:00Z",
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
const admin = (totp_enabled: boolean): Me => ({
  user: {
    id: "u1",
    email: "tushar@example.in",
    display_name: "Tushar",
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

let doc: FakeDocument;
let root: Root | null = null;
let calls: string[] = [];
let done: string[] = [];

type Reply = [number, unknown?] | (() => Promise<Response>);
function serve(routes: Record<string, Reply[]>) {
  calls = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const call = `${init.method ?? "GET"} ${url}`;
    calls.push(call);
    const next = routes[call]?.shift() ?? [500, { error: { code: "error", message: `no route ${call}` } }];
    if (typeof next === "function") return next();
    const [status, body] = next;
    return new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
}

async function show(me: Me, folders: TrashedFolder[], items: DocumentSummary[] = []) {
  done = [];
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  const props = {
    items,
    inTrash: true,
    me,
    now: NOW,
    tree: buildTree([]),
    tags: [],
    suggestions: [],
    movedLeaves: false,
    onRemoved: (message: string) => done.push(message),
    onSaved: () => undefined,
    onFocusList: () => undefined,
    onTagCreated: () => undefined,
    onFolderCreated: () => undefined,
    onUploadVersion: () => undefined,
    trashFolders: folders,
    onOpenFolder: () => undefined,
    onFolderDone: (message: string) => done.push(message),
  } as unknown as Parameters<typeof DocumentsTable>[0];
  await act(async () => root!.render(createElement(DocumentsTable, props)));
  return container;
}

const all = () => doc.body.all();
const byLabel = (label: string) => all().find((element) => element.getAttribute("aria-label") === label) ?? null;
const byId = (id: string) => all().find((element) => element.getAttribute("id") === id) ?? null;
const rowOf = (id: string) => all().find((element) => element.getAttribute("data-id") === id)!;
const cells = (row: FakeElement) => row.childNodes.filter((node): node is FakeElement => node instanceof FakeElement);
const classOf = (element: FakeElement) => element.getAttribute("class") ?? "";
const dialog = () => all().find((element) => element.tagName === "DIALOG") ?? null;
const press = (element: FakeElement | null) => act(async () => void fire(element!, "click", { button: 0 }));
async function type(box: FakeElement | null, text: string) {
  box!.value = text;
  await act(async () => void fire(box!, "input"));
}
const foreverButton = () =>
  dialog()?.all().find((element) => element.tagName === "BUTTON" && element.textContent.startsWith("Delete forever")) ?? null;
async function askForever(name: string) {
  await press(byLabel(`More actions for ${name}`));
  await press(byLabel("Delete forever"));
}

beforeEach(() => {
  doc = install();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  vi.unstubAllGlobals();
});

describe("B16: a folder in Trash is laid out as a document in Trash", () => {
  it("has the same cells as a document row, in the same order", async () => {
    serve({});
    await show(admin(true), [round2], [binned]);
    const folderCells = cells(rowOf("folder:f1")).map(classOf);
    const documentCells = cells(rowOf("d1")).map(classOf);
    const plain = (names: string[]) => names.map((name) => name.replace(/\btabular\b/g, "").trim());
    expect(plain(folderCells)).toEqual(plain(documentCells));
  });

  it("puts its actions behind the same ⋯ menu as a document, inside the actions cell", async () => {
    serve({});
    await show(admin(true), [round2], [binned]);
    const actions = cells(rowOf("folder:f1")).at(-1)!;
    const menu = byLabel("More actions for folder Round 2");
    expect(menu).not.toBeNull();
    expect(actions.contains(menu)).toBe(true);
    expect(classOf(menu!)).toBe(classOf(byLabel("More actions for Bill.pdf")!));
    // No loose buttons beside it: Restore and Delete forever are the menu's, as a document's are.
    expect(actions.all().filter((element) => element.tagName === "BUTTON")).toHaveLength(1);
    await press(menu);
    const items = all().filter((element) => element.getAttribute("role") === "menuitem");
    expect(items.map((item) => item.getAttribute("aria-label"))).toEqual(["Restore", "Delete forever"]);
  });

  it("says where it was and what it holds under its name, each part its own fact so nothing is clipped", async () => {
    serve({});
    await show(admin(true), [round2]);
    const name = cells(rowOf("folder:f1"))[1]!;
    const facts = name.all().find((element) => element.getAttribute("title")?.startsWith("Was in"))!;
    // The facts' line starts a dot early (Documents.module.css): bare text there loses its first letter.
    expect(facts.childNodes.every((node) => node instanceof FakeElement)).toBe(true);
    expect(facts.childNodes.map((node) => node.textContent)).toEqual(["Was in: Top level", "9 files, 26 folders"]);
    expect(facts.getAttribute("title")).toBe("Was in: Top level · 9 files, 26 folders");
    expect(classOf(facts)).toMatch(/facts/);
  });

  it("shows a dash in Status, never the counts", async () => {
    serve({});
    await show(admin(true), [round2]);
    const status = cells(rowOf("folder:f1"))[2]!;
    expect(classOf(status)).toMatch(/statusCell/);
    expect(status.textContent).not.toContain("files");
    expect(status.textContent).toBe("—Not known");
    // Laid out on the row's first line, as a document's status chip is.
    expect(status.all().some((element) => /statusLine/.test(classOf(element)))).toBe(true);
  });

  it("says Just now in Trashed, as a document row does", async () => {
    expect(shortWhen("2026-11-07T10:00:00Z", NOW)).toBe("Just now");
    serve({});
    await show(admin(true), [round2], [binned]);
    const when = (id: string) => rowOf(id).all().find((element) => element.tagName === "TIME")!.textContent;
    expect(when("folder:f1")).toBe("Just now");
    expect(when("d1")).toBe("Just now");
  });

  it("keeps its counts free of zeros", () => {
    expect(heldText({ files: 4, folders: 0 })).toBe("4 files");
    expect(heldText({ files: 0, folders: 2 })).toBe("2 folders");
    expect(heldText({ files: 1, folders: 1 })).toBe("1 file, 1 folder");
    expect(heldText({ files: 0, folders: 0 })).toBe("Empty");
  });

  it("asks before deleting for good in the words a document's question uses", () => {
    expect(folderForeverQuestion({ name: "Old", files: 3 })).toBe("Delete “Old” and its 3 files for good? They can't be brought back.");
    expect(folderForeverQuestion({ name: "Old", files: 1 })).toBe("Delete “Old” and its 1 file for good? It can't be brought back.");
  });

  it("keeps the row's cells on the first line and its status hidden on a card (CSS)", () => {
    const css = readFileSync(join(__dirname, "../components/documents/Documents.module.css"), "utf8");
    expect(css).not.toMatch(/\.folderActions\s*\{/); // the old loose buttons that spilled past the card
    expect(css).toMatch(/\.statusCell\[data-empty\]\s*\{\s*display: none;/);
  });
});

describe("B6, B11, B12: one item's Delete forever", () => {
  const wrong = [400, { error: { code: "bad_request", message: "Your current password is not right." } }] as Reply;

  it("keeps a folder's question open after a wrong password, says why there, and focuses the box", async () => {
    serve({ "POST /api/auth/confirm-password": [wrong] });
    await show(admin(false), [round2]);
    await askForever("folder Round 2");
    await type(byId("forever-folder-password-f1"), "nope");
    await press(foreverButton());
    expect(dialog()).not.toBeNull();
    expect(dialog()!.textContent).toContain("Your current password is not right.");
    expect(doc.activeElement).toBe(byId("forever-folder-password-f1"));
    expect(calls).toEqual(["POST /api/auth/confirm-password"]);
  });

  it("keeps a document's question open after a wrong password, says why there, and focuses the box", async () => {
    serve({ "POST /api/auth/confirm-password": [wrong] });
    await show(admin(false), [], [binned]);
    await askForever("Bill.pdf");
    await type(byId("forever-password-d1"), "nope");
    await press(foreverButton());
    expect(dialog()).not.toBeNull();
    expect(dialog()!.textContent).toContain("Your current password is not right.");
    expect(doc.activeElement).toBe(byId("forever-password-d1"));
  });

  it("closes the folder's question once it worked, and the list hears it", async () => {
    serve({ "DELETE /api/documents/trash/folders/f1/permanent": [[204]] });
    await show(admin(true), [round2]);
    await askForever("folder Round 2");
    await press(foreverButton());
    expect(dialog()).toBeNull();
    expect(done).toEqual(["Deleted Round 2 for good."]);
  });

  it("names each menu action in its visible words", async () => {
    serve({});
    await show(admin(true), [round2]);
    await press(byLabel("More actions for folder Round 2"));
    for (const item of all().filter((element) => element.getAttribute("role") === "menuitem")) {
      const label = item.getAttribute("aria-label") ?? "";
      const visible = item.childNodes.at(-1)!.childNodes[0]!.textContent; // the words before the hint
      expect(label).toContain(visible);
    }
  });
});

describe("B18: Delete forever on a big folder in Trash goes a part at a time", () => {
  it("repeats while the server says 202 {remaining}, saying how many are left, and ends on 204", async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    serve({
      "DELETE /api/documents/trash/folders/f1/permanent": [
        [202, { remaining: 400 }],
        async () => {
          await held;
          return new Response(JSON.stringify({ remaining: 200 }), { status: 202 });
        },
        [204],
      ],
    });
    await show(admin(true), [round2]);
    await askForever("folder Round 2");
    await press(foreverButton());
    expect(dialog()!.textContent).toContain("Deleting… 400 left");
    await act(async () => {
      release();
      await held;
    });
    await act(async () => undefined);
    expect(calls).toHaveLength(3);
    expect(dialog()).toBeNull();
    expect(done).toEqual(["Deleted Round 2 for good."]);
  });

  it("says in plain words how many are still in Trash when a part fails, and keeps the question open", async () => {
    serve({
      "DELETE /api/documents/trash/folders/f1/permanent": [
        [202, { remaining: 300 }],
        [500, { error: { code: "internal_error", message: "Something went wrong on our side." } }],
      ],
    });
    await show(admin(true), [round2]);
    await askForever("folder Round 2");
    await press(foreverButton());
    expect(dialog()).not.toBeNull();
    expect(dialog()!.textContent).toContain("Deleting stopped with 300 files still in Trash.");
    expect(dialog()!.textContent).not.toContain("Something went wrong");
    expect(done).toEqual([]);
  });

  it("stops, instead of asking for ever, when the server keeps saying the same number", async () => {
    serve({
      "DELETE /api/documents/trash/folders/f1/permanent": [
        [202, { remaining: 50 }],
        [202, { remaining: 50 }],
        [202, { remaining: 50 }],
        [202, { remaining: 50 }],
        [202, { remaining: 50 }],
      ],
    });
    await show(admin(true), [round2]);
    await askForever("folder Round 2");
    await press(foreverButton());
    expect(calls.length).toBeLessThanOrEqual(4);
    expect(dialog()!.textContent).toContain("Deleting stopped with 50 files still in Trash.");
  });

  it("repeats for a folder deleted for good from a selection too", async () => {
    serve({
      "DELETE /api/documents/trash/folders/f1/permanent": [[202, { remaining: 10 }], [204]],
    });
    await show(admin(true), [round2]);
    const box = byLabel("Select folder Round 2")!;
    box.checked = true;
    await act(async () => void fire(box, "click", { button: 0 }));
    await press(all().find((element) => element.tagName === "BUTTON" && element.textContent === "Delete forever (1)") ?? null);
    await press(all().find((element) => element.tagName === "BUTTON" && element.textContent.includes("Delete now")) ?? null);
    expect(calls).toEqual([
      "DELETE /api/documents/trash/folders/f1/permanent",
      "DELETE /api/documents/trash/folders/f1/permanent",
    ]);
    expect(done).toEqual(["Deleted 1 folder for good."]);
  });
});

describe("B14: the select-all box in Trash", () => {
  it("is Select all visible, since it ticks folders too", async () => {
    serve({});
    await show(admin(true), [round2], [binned]);
    expect(byLabel("Select all visible")).not.toBeNull();
    expect(byLabel("Select all visible documents")).toBeNull();
  });
});

// --- The reviews' findings ---

describe("Delete forever on a folder in Trash, after the reviews", () => {
  const cancelButton = () =>
    dialog()?.all().find((element) => element.tagName === "BUTTON" && element.textContent === "Cancel") ?? null;

  it("asks about an empty folder with no zero count", () => {
    expect(folderForeverQuestion({ name: "Old", files: 0 })).toBe("Delete “Old” for good? It can't be brought back.");
  });

  it("says in its own words that deleting made no progress", async () => {
    serve({ "DELETE /api/documents/trash/folders/f1/permanent": Array.from({ length: 5 }, () => [202, { remaining: 50 }] as Reply) });
    await show(admin(true), [round2]);
    await askForever("folder Round 2");
    await press(foreverButton());
    expect(dialog()!.textContent).toContain("Deleting made no progress. Try again in a minute.");
  });

  it("puts focus on Delete forever when it stopped part way and no password is asked", async () => {
    serve({
      "DELETE /api/documents/trash/folders/f1/permanent": [[202, { remaining: 300 }], [500, { error: { code: "internal_error" } }]],
    });
    await show(admin(true), [round2]);
    await askForever("folder Round 2");
    await press(foreverButton());
    expect(doc.activeElement).toBe(foreverButton());
  });

  it("on Cancel after it stopped part way, says how many are left and asks for the list again", async () => {
    serve({
      "DELETE /api/documents/trash/folders/f1/permanent": [[202, { remaining: 300 }], [500, { error: { code: "internal_error" } }]],
    });
    await show(admin(true), [round2]);
    await askForever("folder Round 2");
    await press(foreverButton());
    await press(cancelButton());
    expect(dialog()).toBeNull();
    expect(done).toEqual(["Deleting stopped with 300 files still in Trash."]);
  });

  it("hands focus back to the menu on Cancel, even after a Restore that worked", async () => {
    serve({
      "POST /api/documents/trash/folders/f1/restore": [
        [200, { folder: { id: "f1", name: "Round 2", parent_id: null, created_at: "" }, location: [], renamed: false, relocated: false, files: 9, folders: 26 }],
      ],
    });
    await show(admin(true), [round2]);
    await press(byLabel("More actions for folder Round 2"));
    await press(byLabel("Restore"));
    expect(done).toEqual(["Folder restored to Top level."]);
    await askForever("folder Round 2");
    await press(cancelButton());
    expect(doc.activeElement).toBe(byLabel("More actions for folder Round 2"));
  });

  it("stops on a 202 with no count instead of failing as a lost connection", async () => {
    serve({ "DELETE /api/documents/trash/folders/f1/permanent": Array.from({ length: 5 }, () => [202, null] as Reply) });
    await show(admin(true), [round2]);
    await askForever("folder Round 2");
    await press(foreverButton());
    // Nothing was deleted, so the question closes and the row says why.
    expect(rowOf("folder:f1").textContent).toContain("Deleting made no progress.");
    expect(doc.body.textContent).not.toContain("connection");
  });
});
