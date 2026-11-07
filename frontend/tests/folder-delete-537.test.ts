// #537 (Tushar, 7 Nov 2026): Delete on a folder that holds files. Opening it asks the server
// what the folder holds (GET /folders/{id}/summary): an empty folder keeps today's simple
// confirm; a member or reviewer is told up front to ask an admin (no Delete that can only fail);
// an admin chooses: move what is inside to a folder they pick (never the folder itself or one in
// it), or send everything to Trash after typing the folder's name. The words, the calls, and the
// screen itself, rendered with react-dom in the small fake DOM (tests/fake-dom.ts) and pressed.

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildTree } from "@/components/documents/folderTree";
import {
  RESTORE_DAYS,
  heldLine,
  keptMessage,
  notEmptyText,
  trashButton,
  trashedMessage,
} from "@/components/documents/folderDelete";
import { FolderActions } from "@/components/documents/FolderActions";
import { deleteFolder, deleteFolderWithContents, folderSummary } from "@/lib/api";
import type { Folder, FolderSummary } from "@/lib/types";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

const folder = (id: string, name: string, parent_id: string | null = null, kind: Folder["kind"] = "custom"): Folder => ({
  id,
  name,
  parent_id,
  created_at: "2026-11-07T10:00:00Z",
  kind,
});
const FOLDERS = [
  folder("p", "Accounts"),
  folder("f", "Bills", "p"),
  folder("f-in", "Old bills", "f"),
  folder("s", "Suppliers"),
  folder("d", "Sales", null, "department"),
  folder("d-in", "Invoices", "d", "category"),
];
const TREE = buildTree(FOLDERS);
const FULL: FolderSummary = { files: 3, folders: 1, bytes: 1536 };
const EMPTY: FolderSummary = { files: 0, folders: 0, bytes: 0 };
const MOVED = { files: 3, folders: 1 }; // the server's reply: what moved, or the files trashed
const TRASHED = { files: 3, folders: 1 };

describe("the words", () => {
  it("counts what the folder holds, leaving out what it has none of", () => {
    expect(heldLine({ files: 3, folders: 2, bytes: 1536 })).toBe("It holds 3 files and 2 folders (1.5 KB).");
    expect(heldLine({ files: 1, folders: 1, bytes: 10 })).toBe("It holds 1 file and 1 folder (10 B).");
    expect(heldLine({ files: 2, folders: 0, bytes: 2048 })).toBe("It holds 2 files (2 KB).");
    expect(heldLine({ files: 0, folders: 4, bytes: 0 })).toBe("It holds 4 folders.");
  });

  it("names the Trash button by the files it moves, and says what happened after", () => {
    expect(trashButton(3, "folder")).toBe("Move 3 files to Trash");
    expect(trashButton(1, "department")).toBe("Move 1 file to Trash");
    expect(trashButton(0, "department")).toBe("Delete department"); // only empty folders inside
    expect(keptMessage("folder", "Accounts", 2)).toBe("Folder deleted. Its files moved to Accounts.");
    expect(keptMessage("department", null, 2)).toBe("Department deleted. Its files moved to the top level.");
    expect(keptMessage("folder", "Accounts", 0)).toBe("Folder deleted. What was inside moved to Accounts.");
    expect(trashedMessage("folder", 3)).toBe("Folder deleted. 3 files moved to Trash.");
    expect(trashedMessage("department", 1)).toBe("Department deleted. 1 file moved to Trash.");
    expect(trashedMessage("folder", 0)).toBe("Folder deleted."); // only empty folders were inside
    expect(notEmptyText("folder")).toBe("This folder isn't empty. Ask an admin to delete it, or move what is inside first.");
    expect(RESTORE_DAYS).toBe(30); // TRASH_DAYS's default; the browser is not told the setting
  });
});

let doc: FakeDocument;
let root: Root | null = null;
let calls: { url: string; method: string }[] = [];

/** fetch answers each "METHOD /api/path" from `routes`: a body (200), null (204) or an error. */
function serve(routes: Record<string, unknown>) {
  calls = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const method = init.method ?? "GET";
    calls.push({ url, method });
    const reply = routes[`${method} ${url}`];
    if (reply === undefined) throw new Error(`no route for ${method} ${url}`);
    if (reply === null) return new Response(null, { status: 204 });
    if (reply instanceof Promise) return reply as Promise<Response>;
    const failed = (reply as { error?: unknown }).error !== undefined;
    return new Response(JSON.stringify(reply), { status: failed ? 409 : 200, headers: { "Content-Type": "application/json" } });
  });
}

async function show(folderId: string, admin: boolean, summary: FolderSummary, routes: Record<string, unknown> = {}) {
  serve({ [`GET /api/folders/${folderId}/summary`]: summary, ...routes });
  const onChanged = vi.fn();
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  const props = { tree: TREE, current: TREE.byId.get(folderId)!, onChanged, admin };
  await act(async () => root!.render(createElement(FolderActions, props)));
  await press(button(container, "Delete this folder"));
  await settle(); // the summary's answer
  return { screen: container, onChanged };
}

const all = (within: FakeElement) => within.all();
/** The button that reads `text`; a busy label's words are there twice (BusyLabel). */
const button = (within: FakeElement, text: string) =>
  all(within).find(
    (element) => element.tagName === "BUTTON" && [text, text + text].includes(element.textContent.trim()),
  ) ?? null;
/** The radio or text box whose <label> reads `text` (a picker row's hidden ", Level n" aside). */
const control = (within: FakeElement, text: string) => {
  const label = all(within).find(
    (element) => element.tagName === "LABEL" && element.textContent.replace(/, Level \d+$/, "").trim() === text,
  );
  if (!label) return null;
  const forId = label.getAttribute("for");
  return forId
    ? (all(within).find((element) => element.getAttribute("id") === forId) ?? null)
    : (label.all().find((element) => element.tagName === "INPUT") ?? null);
};
const settle = () => act(async () => void (await new Promise((resolve) => setTimeout(resolve, 0))));
const press = async (element: FakeElement | null) => {
  await act(async () => void fire(element!, "click", { button: 0 }));
  await settle(); // a call's answer
};
async function choose(radio: FakeElement | null) {
  radio!.checked = true;
  await act(async () => void fire(radio!, "click", { button: 0 }));
}
async function type(box: FakeElement | null, text: string) {
  box!.value = text;
  await act(async () => void fire(box!, "input"));
}

beforeEach(() => {
  doc = install();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  vi.unstubAllGlobals();
});

describe("the calls", () => {
  it("asks for the summary, and deletes with what to do with the contents", async () => {
    serve({
      "GET /api/folders/f/summary": FULL,
      "DELETE /api/folders/f?contents=keep&to=s": MOVED,
      "DELETE /api/folders/f?contents=keep": MOVED,
      "DELETE /api/folders/f?contents=trash": TRASHED,
      "DELETE /api/folders/f": null,
    });
    expect(await folderSummary("f")).toEqual(FULL);
    expect(await deleteFolderWithContents("f", { contents: "keep", to: "s" })).toEqual(MOVED);
    await deleteFolderWithContents("f", { contents: "keep", to: null }); // the top level
    expect(await deleteFolderWithContents("f", { contents: "trash" })).toEqual(TRASHED);
    expect(await deleteFolder("f")).toBeUndefined();
    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([
      "GET /api/folders/f/summary",
      "DELETE /api/folders/f?contents=keep&to=s",
      "DELETE /api/folders/f?contents=keep",
      "DELETE /api/folders/f?contents=trash",
      "DELETE /api/folders/f",
    ]);
  });
});

describe("the screen: by role and by what the folder holds", () => {
  it("keeps today's simple confirm for an empty folder, for anyone", async () => {
    for (const admin of [true, false]) {
      const { screen, onChanged } = await show("f", admin, EMPTY, { "DELETE /api/folders/f": null });
      expect(screen.textContent).toContain("Delete the folder “Bills”? Only an empty folder can be deleted.");
      expect(all(screen).some((element) => element.tagName === "DIALOG")).toBe(false);
      expect(button(screen, "Delete folder")!.getAttribute("type")).toBe("submit");
      await act(async () => void fire(all(screen).find((element) => element.tagName === "FORM")!, "submit"));
      await settle();
      expect(calls.at(-1)).toEqual({ url: "/api/folders/f", method: "DELETE" });
      expect(onChanged).toHaveBeenCalledWith("Deleted the folder Bills.", { folderId: "p" });
      await act(async () => root?.unmount());
    }
  });

  it("tells a member up front that a folder with files needs an admin, with no Delete to press", async () => {
    const { screen } = await show("f", false, FULL);
    expect(screen.textContent).toContain(notEmptyText("folder"));
    expect(button(screen, "Delete folder")).toBeNull();
    expect(button(screen, "Close")).not.toBeNull();
    expect(calls).toEqual([{ url: "/api/folders/f/summary", method: "GET" }]);
  });

  it("shows an admin the two choices at once, with the counts, moving to the parent by default", async () => {
    const { screen } = await show("f", true, FULL);
    const dialog = all(screen).find((element) => element.tagName === "DIALOG")!;
    expect(dialog.open).toBe(true);
    expect(dialog.textContent).toContain("Delete “Bills”?");
    expect(dialog.textContent).toContain("It holds 3 files and 1 folder (1.5 KB).");
    expect(control(dialog, "Move what’s inside, then delete the folder")!.checked).toBe(true);
    expect(control(dialog, "Delete the folder and everything inside")!.checked).toBe(false);
    expect(control(dialog, "Accounts")!.checked).toBe(true); // the parent
    expect(control(dialog, "Top level")).not.toBeNull();
    expect(button(dialog, "Move and delete folder")).not.toBeNull();
    expect(doc.activeElement).toBe(button(dialog, "Cancel")); // never the destructive button
  });

  it("never offers the folder itself or a folder inside it as the place to move to", async () => {
    const { screen } = await show("f", true, FULL);
    const dialog = all(screen).find((element) => element.tagName === "DIALOG")!;
    expect(control(dialog, "Bills")!.getAttribute("disabled")).not.toBeNull();
    expect(control(dialog, "Suppliers")!.getAttribute("disabled")).toBeNull();
    // Open Bills in the picker: what is inside it can't be chosen either.
    await press(all(dialog).find((element) => element.getAttribute("aria-label") === "Show folders in Bills")!);
    expect(control(dialog, "Old bills")!.getAttribute("disabled")).not.toBeNull();
  });
});

describe("the screen: an answer that comes late", () => {
  it("drops a summary that fails after the person moved on to Rename", async () => {
    let fail: (reply: Response) => void = () => undefined;
    const late = new Promise<Response>((resolve) => (fail = resolve));
    const { screen } = await show("f", true, FULL, { "GET /api/folders/f/summary": late });
    await press(button(screen, "Rename this folder"));
    const error = { error: { code: "internal_error", message: "Something went wrong on our side." } };
    await act(async () => fail(new Response(JSON.stringify(error), { status: 500 })));
    await settle();
    expect(all(screen).some((element) => element.getAttribute("role") === "alert")).toBe(false);
    expect(control(screen, "New name for “Bills”")).not.toBeNull();
  });
});

describe("the screen: deleting", () => {
  it("moves what is inside to the folder chosen, then says where", async () => {
    const { screen, onChanged } = await show("f", true, FULL, { "DELETE /api/folders/f?contents=keep&to=s": MOVED });
    const dialog = all(screen).find((element) => element.tagName === "DIALOG")!;
    await choose(control(dialog, "Suppliers"));
    await press(button(dialog, "Move and delete folder"));
    expect(calls.at(-1)).toEqual({ url: "/api/folders/f?contents=keep&to=s", method: "DELETE" });
    expect(onChanged).toHaveBeenCalledWith("Folder deleted. Its files moved to Suppliers.", { folderId: "p" }, true);
  });

  it("moves to the top level when chosen", async () => {
    const { screen, onChanged } = await show("f", true, FULL, { "DELETE /api/folders/f?contents=keep": MOVED });
    const dialog = all(screen).find((element) => element.tagName === "DIALOG")!;
    await choose(control(dialog, "Top level"));
    await press(button(dialog, "Move and delete folder"));
    expect(calls.at(-1)).toEqual({ url: "/api/folders/f?contents=keep", method: "DELETE" });
    expect(onChanged).toHaveBeenCalledWith("Folder deleted. Its files moved to the top level.", { folderId: "p" }, true);
  });

  it("sends everything to Trash only once the folder's name is typed exactly", async () => {
    // The server trashed 4: one arrived after the dialog opened. The toast says what happened.
    const four = { files: 4, folders: 1 };
    const { screen, onChanged } = await show("f", true, FULL, { "DELETE /api/folders/f?contents=trash": four });
    const dialog = all(screen).find((element) => element.tagName === "DIALOG")!;
    await choose(control(dialog, "Delete the folder and everything inside"));
    // Changed on purpose (#542): Trash keeps the folder, so the folder is what comes back.
    expect(dialog.textContent).toContain(`Everything goes to Trash. You can restore the folder for ${RESTORE_DAYS} days.`);
    const confirm = () => button(dialog, "Move 3 files to Trash")!;
    const box = control(dialog, "Type Bills to confirm");
    expect(confirm().getAttribute("disabled")).not.toBeNull();
    for (const wrong of ["bills", "Bills ", "Bil"]) {
      await type(box, wrong);
      expect(confirm().getAttribute("disabled")).not.toBeNull();
    }
    await type(box, "Bills");
    expect(confirm().getAttribute("disabled")).toBeNull();
    await press(confirm());
    expect(calls.at(-1)).toEqual({ url: "/api/folders/f?contents=trash", method: "DELETE" });
    expect(onChanged).toHaveBeenCalledWith("Folder deleted. 4 files moved to Trash.", { folderId: "p" }, true);
  });

  it("takes the name typed in either Unicode form, as a Mac's folder names come", async () => {
    const mac = buildTree([folder("m", "Cafe\u0301")]); // "Café", decomposed
    serve({ "GET /api/folders/m/summary": FULL, "DELETE /api/folders/m?contents=trash": TRASHED });
    const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
    const { createRoot } = await import("react-dom/client");
    root = createRoot(container as unknown as Element);
    const props = { tree: mac, current: mac.byId.get("m")!, onChanged: vi.fn(), admin: true };
    await act(async () => root!.render(createElement(FolderActions, props)));
    await press(button(container, "Delete this folder"));
    await settle();
    const dialog = all(container).find((element) => element.tagName === "DIALOG")!;
    await choose(control(dialog, "Delete the folder and everything inside"));
    const box = all(dialog).find((element) => element.tagName === "INPUT" && element.type === "text")!;
    await type(box, "Caf\u00e9"); // typed on Windows: one character
    expect(button(dialog, "Move 3 files to Trash")!.getAttribute("disabled")).toBeNull();
  });

  it("says department for a department, everywhere", async () => {
    const { screen, onChanged } = await show("d", true, FULL, { "DELETE /api/folders/d?contents=trash": TRASHED });
    const dialog = all(screen).find((element) => element.tagName === "DIALOG")!;
    expect(control(dialog, "Move what’s inside, then delete the department")).not.toBeNull();
    expect(button(dialog, "Move and delete department")).not.toBeNull();
    await choose(control(dialog, "Delete the department and everything inside"));
    await type(control(dialog, "Type Sales to confirm"), "Sales");
    await press(button(dialog, "Move 3 files to Trash"));
    expect(onChanged).toHaveBeenCalledWith("Department deleted. 3 files moved to Trash.", { folderId: null }, true);
  });

  it("keeps the dialog open with the server's plain words when it refuses", async () => {
    const running = {
      error: { code: "folder_upload_running", message: "An upload into this folder is still running. Try again when it finishes." },
    };
    const { screen, onChanged } = await show("f", true, FULL, { "DELETE /api/folders/f?contents=keep&to=p": running });
    const dialog = all(screen).find((element) => element.tagName === "DIALOG")!;
    await press(button(dialog, "Move and delete folder"));
    const alert = all(dialog).find((element) => element.getAttribute("role") === "alert")!;
    expect(alert.textContent).toBe("An upload into this folder is still running. Try again when it finishes.");
    expect(dialog.open).toBe(true);
    expect(onChanged).not.toHaveBeenCalled();
  });
});
