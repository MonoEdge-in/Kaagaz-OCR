// #725 Ask from a chosen file, and Ask in the folder you are in. A first-time user always sees
// whether they choose a department, a folder or a file: the find box groups its matches under
// Departments, Folders and Files, each with its own icon and place; the chip says exactly what is
// chosen, with a remove button naming it. Files toggle in and out, up to 50. The Documents folder
// view's "Ask in this folder" opens /chat?folder=. The words and choices in node; the find box
// rendered with react-dom in the small fake DOM (tests/fake-dom.ts) and pressed.

import { readFileSync } from "node:fs";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { askInFolderHref, folderFromSearch } from "@/components/chat/logic";
import {
  MAX_FILES,
  filePlace,
  findFolders,
  pickFile,
  removeScopeLabel,
  scopeText,
  startScope,
} from "@/components/chat/scope";
import { buildTree } from "@/components/documents/folderTree";
import type { DocumentSummary, Folder } from "@/lib/types";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

const api = vi.hoisted(() => ({ listDocuments: vi.fn() }));
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), ...api }));

const folders: Folder[] = [
  { id: "d1", name: "Land & Legal", parent_id: null, created_at: "2026-10-01", kind: "department" },
  { id: "f2", name: "Land records", parent_id: "d1", created_at: "2026-10-01" },
  { id: "f3", name: "Old land maps", parent_id: null, created_at: "2026-10-01" },
];
const tree = buildTree(folders);
const file = (id: string, name: string, more: Partial<DocumentSummary> = {}) =>
  ({ id, display_name: name, folder_id: "f2", shared_with_you: false, ...more }) as DocumentSummary;
const FOLDER_ID = "0b6f2a4e-1c2d-4e5f-8a9b-0c1d2e3f4a5b";

describe("what is chosen, in words", () => {
  it("says a department, a folder with its subfolders, one file or several", () => {
    expect(scopeText({ kind: "folder", folderId: "d1", subfolders: true }, tree, null)).toBe("Ask in department: Land & Legal");
    expect(scopeText({ kind: "folder", folderId: "f2", subfolders: true }, tree, null)).toBe(
      "Ask in folder: Land & Legal › Land records (and its subfolders)",
    );
    expect(scopeText({ kind: "documents", ids: ["a"] }, null, "Sale Deed 1337-2004.pdf")).toBe(
      "Ask about file: Sale Deed 1337-2004.pdf",
    );
    expect(scopeText({ kind: "documents", ids: ["a", "b", "c"] }, null, null)).toBe("Ask about 3 files");
  });

  it("names what the remove button takes away", () => {
    expect(removeScopeLabel({ kind: "folder", folderId: "d1", subfolders: true }, tree, null)).toBe("Remove department Land & Legal");
    expect(removeScopeLabel({ kind: "folder", folderId: "f2", subfolders: true }, tree, null)).toBe("Remove folder Land records");
    expect(removeScopeLabel({ kind: "documents", ids: ["a"] }, null, "bill.pdf")).toBe("Remove file bill.pdf");
    expect(removeScopeLabel({ kind: "documents", ids: ["a", "b"] }, null, null)).toBe("Remove 2 files");
    expect(removeScopeLabel({ kind: "all" }, tree, null)).toBeNull();
    expect(removeScopeLabel({ kind: "folder", folderId: "gone", subfolders: true }, tree, null)).toBeNull();
  });

  it("finds departments and folders apart, and says where a file is", () => {
    expect(findFolders(tree, "land").map((found) => [found.name, found.department])).toEqual([
      ["Land & Legal", true],
      ["Land records", false],
      ["Old land maps", false],
    ]);
    expect(filePlace(tree, file("a", "a.pdf"))).toBe("Land & Legal › Land records");
    expect(filePlace(tree, file("a", "a.pdf", { folder_id: null }))).toBe("Not in a folder");
    expect(filePlace(tree, file("a", "a.pdf", { folder_id: null, shared_with_you: true }))).toBe("Shared with you");
  });

  it("toggles a file in and out, at most 50", () => {
    expect(pickFile([], "a")).toEqual(["a"]);
    expect(pickFile(["a", "b"], "a")).toEqual(["b"]);
    const full = Array.from({ length: MAX_FILES }, (_, n) => `f${n}`);
    expect(pickFile(full, "new")).toBeNull();
    expect(pickFile(full, "f3")).toHaveLength(MAX_FILES - 1);
  });
});

describe("Ask in this folder", () => {
  it("links to Ask limited to the folder, and Ask reads only a well-formed id", () => {
    expect(askInFolderHref(FOLDER_ID)).toBe(`/chat?folder=${FOLDER_ID}`);
    expect(folderFromSearch(`?folder=${FOLDER_ID}`)).toBe(FOLDER_ID);
    expect(folderFromSearch("?folder=../x")).toBeNull();
    expect(folderFromSearch(`?c=${FOLDER_ID}&folder=${FOLDER_ID}`)).toBeNull(); // a conversation keeps its own
    expect(startScope(null, FOLDER_ID).scope).toEqual({ kind: "folder", folderId: FOLDER_ID, subfolders: true });
  });

  it("is a secondary button on the folder view, a department's saying so", () => {
    const view = readFileSync("components/documents/DocumentsView.tsx", "utf8");
    expect(view).toMatch(
      /<Link className=\{`btn \$\{libraryStyles\.askHere\}`\} href=\{askInFolderHref\(current\.folder\.id\)\}>\s*\{isDepartment\(current\.folder\) \? "Ask in this department" : "Ask in this folder"\}/,
    );
    const css = readFileSync("components/documents/Library.module.css", "utf8");
    expect(css).toMatch(/\.askHere \{[^}]*min-height: var\(--h-lg\);/); // 44px
  });
});

// --- the find box, pressed ---

let doc: FakeDocument;
let root: Root | null = null;

beforeEach(() => {
  doc = install();
  vi.useFakeTimers();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  vi.useRealTimers();
  vi.resetAllMocks();
});

const all = () => doc.body.all();
const headings = () => all().filter((element) => element.tagName === "H3").map((element) => element.textContent);
const rowsOf = (group: string) =>
  all().find((element) => element.tagName === "UL" && element.getAttribute("aria-label") === group)?.all().filter((e) => e.tagName === "BUTTON") ?? [];

async function showFind(scope: Parameters<typeof import("@/components/chat/ScopeFind").ScopeFind>[0]["scope"]) {
  const { ScopeFind } = await import("@/components/chat/ScopeFind");
  const chosen: unknown[] = [];
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  await act(async () => root!.render(createElement(ScopeFind, { id: "s", tree, scope, onChoose: (pick) => chosen.push(pick) })));
  const box = all().find((element) => element.tagName === "INPUT")!;
  const type = async (words: string) => {
    box.value = words;
    await act(async () => void fire(box, "input"));
    await act(async () => void (await vi.advanceTimersByTimeAsync(300)));
  };
  return { chosen, type };
}

describe("the find box", () => {
  it("groups Departments, then Folders, then Files, each row with its own icon and place", async () => {
    api.listDocuments.mockResolvedValue({ items: [file("x1", "Land deed.pdf")], total: 1 });
    const { type } = await showFind({ kind: "all" });
    expect(all().some((element) => element.textContent === "Find a department, folder or file by name.")).toBe(true);
    await type("land");
    expect(api.listDocuments).toHaveBeenCalledWith({ q: "land", limit: 10, status: "ready" });
    expect(headings()).toEqual(["Departments", "Folders", "Files"]);
    const [department] = rowsOf("Departments");
    const [folder] = rowsOf("Folders");
    const [found] = rowsOf("Files");
    expect([department, folder, found].map((row) => row!.textContent)).toEqual([
      "Land & LegalLand & Legal",
      "Land recordsLand & Legal › Land records",
      "Land deed.pdfLand & Legal › Land records",
    ]);
    const icons = [department, folder, found].map((row) => JSON.stringify(row!.all().filter((e) => e.tagName.toLowerCase() === "svg").map((svg) => svg.all().map((p) => p.getAttribute("d")))));
    expect(new Set(icons).size).toBe(3); // three different icons
  });

  it("skips an empty group, and says plainly when nothing matches and how to go back", async () => {
    api.listDocuments.mockResolvedValue({ items: [], total: 0 });
    const { type } = await showFind({ kind: "all" });
    await type("records");
    expect(headings()).toEqual(["Folders"]);
    await type("zebra");
    expect(headings()).toEqual([]);
    expect(doc.body.textContent).toContain(
      "No department, folder or file matches “zebra”. To ask about everything, choose All documents above.",
    );
  });

  it("picks a department or folder with its subfolders, and files one by one", async () => {
    api.listDocuments.mockResolvedValue({ items: [file("x1", "Land deed.pdf"), file("x2", "Land map.pdf")], total: 2 });
    const { chosen, type } = await showFind({ kind: "documents", ids: ["x1"] });
    await type("land");
    const [first, second] = rowsOf("Files");
    expect([first!.getAttribute("aria-pressed"), second!.getAttribute("aria-pressed")]).toEqual(["true", "false"]);
    await act(async () => void fire(second!, "click", { button: 0 }));
    await act(async () => void fire(first!, "click", { button: 0 }));
    expect(chosen).toEqual([
      { kind: "documents", ids: ["x1", "x2"] },
      { kind: "all" }, // x1 out, none left: every document
    ]);
    await act(async () => void fire(rowsOf("Departments")[0]!, "click", { button: 0 }));
    expect(chosen[2]).toEqual({ kind: "folder", folderId: "d1", subfolders: true });
  });
});
