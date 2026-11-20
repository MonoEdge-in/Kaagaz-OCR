// #720 Ask inside a folder (design board 3): "Find a folder by name or path" in the scope picker
// picks a folder with its subfolders, and the chip's remove button goes back to all documents.
// The match in node; the picker rendered with react-dom in the small fake DOM (tests/fake-dom.ts).

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { findFolders } from "@/components/chat/scope";
import { buildTree } from "@/components/documents/folderTree";
import type { Folder } from "@/lib/types";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

const folders: Folder[] = [
  { id: "f1", name: "Land & Legal", parent_id: null, created_at: "2026-10-01" },
  { id: "f2", name: "Land records", parent_id: "f1", created_at: "2026-10-01" },
  { id: "f3", name: "Survey 44, Wakad", parent_id: "f2", created_at: "2026-10-01" },
  { id: "f4", name: "Sale deeds", parent_id: "f1", created_at: "2026-10-01" },
];
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  listFolders: async () => ({ items: folders }),
}));

describe("find a folder by name or path", () => {
  const tree = buildTree(folders);

  it("matches a name or a path, any case, in tree order, at most the cap", () => {
    expect(findFolders(tree, "land REC").map((f) => f.path)).toEqual([
      "Land & Legal / Land records",
      "Land & Legal / Land records / Survey 44, Wakad",
    ]);
    expect(findFolders(tree, "wakad")).toEqual([
      { id: "f3", name: "Survey 44, Wakad", path: "Land & Legal / Land records / Survey 44, Wakad" },
    ]);
    expect(findFolders(tree, "land", 2)).toHaveLength(2);
    expect(findFolders(tree, "   ")).toEqual([]);
  });
});

let doc: FakeDocument;
let root: Root | null = null;

beforeEach(() => {
  doc = install();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
});

const all = () => doc.body.all();
const press = (element: FakeElement | null | undefined) => act(async () => void fire(element!, "click", { button: 0 }));

describe("the scope picker, pressed", () => {
  it("picks the folder with its subfolders, and the chip's button removes it", async () => {
    const { ScopePicker } = await import("@/components/chat/ScopePicker");
    const chosen: unknown[] = [];
    const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
    const { createRoot } = await import("react-dom/client");
    root = createRoot(container as unknown as Element);
    const state = { scope: { kind: "folder", folderId: "f4", subfolders: false } as const, changed: false, documents: null, announce: false };
    await act(async () => root!.render(createElement(ScopePicker, { state, locked: null, onChoose: (pick) => chosen.push(pick) })));
    await act(async () => undefined); // the folders arrive
    const box = all().find((element) => element.tagName === "INPUT" && element.type === "search")!;
    box.value = "survey";
    await act(async () => void fire(box, "input"));
    await press(all().find((element) => element.tagName === "BUTTON" && element.textContent.startsWith("Survey 44, Wakad")));
    expect(chosen).toEqual([{ kind: "folder", folderId: "f3", subfolders: true }]);
    await press(all().find((element) => element.getAttribute("aria-label") === "Remove folder Sale deeds"));
    expect(chosen[1]).toEqual({ kind: "all" });
  });
});
