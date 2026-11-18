// PRD 9 G5 (Filing and folders). F-18: a visible "New folder" beside Rename and Move in a
// department or folder, for anyone who can upload there (every role: DocumentsView's canUpload is
// not by role; not in Trash or Shared), refusing a name already there. F-02: "Let Kaagaz sort
// files" sends files into the department being viewed, where Kaagaz files them. F-01: the
// suggestion row and the Not sorted marker. Rendered in node with the fake DOM.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FolderActions } from "@/components/documents/FolderActions";
import { DepartmentSuggestion } from "@/components/documents/UploadArea";
import { buildTree } from "@/components/documents/folderTree";
import { sortInto } from "@/components/files/departments";
import type { DocumentSummary, Folder } from "@/lib/types";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

const folder = (id: string, name: string, parent_id: string | null = null, kind: Folder["kind"] = "custom"): Folder => ({
  id,
  name,
  parent_id,
  created_at: "2026-11-18T10:00:00Z",
  kind,
});
const TREE = buildTree([folder("legal", "Land & Legal", null, "department"), folder("land", "Land records", "legal"), folder("loose", "Loose")]);
const read = (file: string) => readFileSync(join(__dirname, "..", file), "utf8");

let doc: FakeDocument;
let root: Root | null = null;
let calls: { url: string; method: string; body: unknown }[] = [];

/** Each "METHOD /api/path" answers `{status, body}` (200 when a plain body). */
function serve(routes: Record<string, unknown>) {
  calls = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    calls.push({ url, method, body: init.body ? JSON.parse(String(init.body)) : undefined });
    const reply = routes[`${method} ${url}`];
    if (reply === undefined) throw new Error(`no route for ${method} ${url}`);
    const { status, body } = (reply as { status?: number }).status ? (reply as { status: number; body: unknown }) : { status: 200, body: reply };
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  });
}
const settle = () => act(async () => void (await new Promise((resolve) => setTimeout(resolve, 0))));
const button = (within: FakeElement, text: string) =>
  // BusyLabel keeps both words in the button (one hidden), so "Move there" may read twice.
  within.all().find((element) => element.tagName === "BUTTON" && [text, text + text].includes(element.textContent.trim())) ?? null;

async function mount(element: ReturnType<typeof createElement>) {
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  await act(async () => root!.render(element));
  await settle();
  return container;
}

beforeEach(() => {
  doc = install();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  vi.unstubAllGlobals();
});

describe("F-18: New folder in the place header", () => {
  it.each([
    ["a department, for an admin", "legal", true],
    ["a department, for a member or reviewer", "legal", false],
    ["a folder, for an admin", "land", true],
    ["a folder, for a member or reviewer", "land", false],
  ])("shows New folder beside Rename and Move in %s", async (_, place, admin) => {
    const screen = await mount(createElement(FolderActions, { tree: TREE, current: TREE.byId.get(place)!, onChanged: vi.fn(), admin }));
    const words = screen.all().filter((element) => element.tagName === "BUTTON").map((element) => element.textContent.trim());
    expect(words.slice(0, 3)).toEqual(["New folder here", "Rename this folder", "Move this folder"]);
  });

  it("has no such button at All documents, where the folder column's + makes one at the top", async () => {
    const screen = await mount(createElement(FolderActions, { tree: TREE, current: null, onChanged: vi.fn() }));
    expect(button(screen, "New folder here")).toBeNull();
    expect(read("components/documents/DocumentsView.tsx")).toContain('<IconButton icon="plus" label="New folder"');
  });

  it("is shown only where the person can upload: not in Trash or Shared", () => {
    expect(read("components/documents/DocumentsView.tsx")).toContain("const canUpload = !folderGone && !library.trash && !library.shared;");
    expect(read("components/documents/DocumentsView.tsx")).toMatch(/\{folders !== null && canUpload && \(\s*<FolderActions/);
  });

  it("makes the folder inside the place, and refuses a name already there", async () => {
    serve({
      "POST /api/folders": { status: 409, body: { error: { code: "conflict", message: "Taken." } } },
    });
    const onChanged = vi.fn();
    const screen = await mount(createElement(FolderActions, { tree: TREE, current: TREE.byId.get("legal")!, onChanged }));
    await act(async () => void fire(button(screen, "New folder here")!, "click", { button: 0 }));
    await settle();
    const box = screen.all().find((element) => element.tagName === "INPUT")!;
    expect(screen.textContent).toContain("Name of the new folder in “Land & Legal”");
    box.value = "Land records";
    await act(async () => void fire(box, "input"));
    await act(async () => void fire(box.closest("form")!, "submit"));
    await settle();
    expect(calls).toEqual([{ url: "/api/folders", method: "POST", body: { name: "Land records", parent_id: "legal" } }]);
    expect(screen.textContent).toContain("A folder with this name is already here.");
    expect(onChanged).not.toHaveBeenCalled();
  });
});

describe("F-02: Let Kaagaz sort files goes into the department being viewed", () => {
  it("sends the department of the place shown, or the one picked at the top; none elsewhere", () => {
    expect(sortInto(TREE, "legal", null)).toBe("legal");
    expect(sortInto(TREE, "land", null)).toBe("legal"); // a folder inside it: its department
    expect(sortInto(TREE, "loose", "legal")).toBeNull(); // a folder in no department
    expect(sortInto(TREE, null, "legal")).toBe("legal"); // All documents, a department picked
    expect(sortInto(TREE, null, null)).toBeNull();
    expect(read("components/documents/DocumentsView.tsx")).toContain("sortInto(tree, library.folderId, department) ?? undefined");
  });
});

const summary = (more: Partial<DocumentSummary>): DocumentSummary =>
  ({ id: "d1", status: "ready", folder_id: null, autofile: null, suggested_department: null, ...more }) as DocumentSummary;

describe("F-01: Looks like <department>", () => {
  it("offers the department, and Move there moves the file into it as the Move dialog does", async () => {
    serve({ "PATCH /api/documents/d1": { status: 200, body: summary({ folder_id: "legal" }) } });
    const onMoved = vi.fn();
    const screen = await mount(
      createElement(DepartmentSuggestion, {
        document: summary({ suggested_department: { id: "legal", name: "Land & Legal" } }),
        tree: TREE,
        onMoved,
      }),
    );
    expect(screen.textContent).toContain("Not sorted. Looks like Land & Legal");
    await act(async () => void fire(button(screen, "Move there")!, "click", { button: 0 }));
    await settle();
    expect(calls).toEqual([{ url: "/api/documents/d1", method: "PATCH", body: { folder_id: "legal" } }]);
    expect(onMoved).toHaveBeenCalledTimes(1);
    expect(screen.textContent).toContain("Moved to Land & Legal.");
  });

  it("says nothing when there is no suggestion", async () => {
    const screen = await mount(
      createElement(DepartmentSuggestion, { document: summary({}), tree: TREE, onMoved: vi.fn() }),
    );
    expect(screen.textContent).toBe("");
  });

  it("shows the whole path in Filed into, and Not sorted in the list for a file in no folder or not filed", () => {
    expect(read("components/documents/UploadArea.tsx")).toContain("filedText(pathLabel(tree, ready.folder_id))");
    expect(read("components/documents/DocumentsTable.tsx")).toContain('notSorted && "Not sorted"');
  });
});
