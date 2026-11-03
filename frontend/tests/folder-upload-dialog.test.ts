// Upload a folder (#484): the dialog before Start (what was read, where it goes, a same-name
// folder already there, what stops it), the tray's row for a folder upload, and the activity words.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { describeAction } from "@/components/admin/logic";
import { activityWords } from "@/components/activity/words";
import { buildManifest, fromFileList } from "@/components/documents/folderPick";
import type { Manifest } from "@/components/documents/folderPick";
import { FolderUploadDialog, clashOf, dialogProblem, summaryLines } from "@/components/documents/FolderUploadDialog";
import { FolderUploadRow } from "@/components/documents/FolderUploadRow";
import { buildTree } from "@/components/documents/folderTree";
import { EMPTY_FOLDER_UPLOADS, folderUploadsReducer } from "@/components/documents/folderUpload";
import type { FolderUploadLimits, Folder } from "@/lib/types";

const LIMITS: FolderUploadLimits = {
  max_files: 50000,
  max_bytes: 5 * 1024 ** 3,
  max_folders: 10000,
  max_depth: 100,
  calls_per_minute: 600,
};
const folder = (id: string, name: string, parent_id: string | null = null, kind: Folder["kind"] = "custom"): Folder => ({
  id,
  name,
  parent_id,
  created_at: "2026-11-03T10:00:00Z",
  kind,
});
function picked(path: string, size = 10): File {
  const file = new File([new Uint8Array(size)], path.split("/").at(-1) ?? path);
  Object.defineProperty(file, "webkitRelativePath", { value: path });
  return file;
}
const manifest = (over: Partial<Manifest>): Manifest => ({
  folders: [{ name: "A", parent: null }],
  fileFolder: new Int32Array(0),
  fileCount: 10,
  totalBytes: 100,
  height: 1,
  badName: null,
  ...over,
});

describe("what stops a folder upload before anything is sent", () => {
  it("is too deep for the place chosen, counting that place's own depth", () => {
    expect(dialogProblem(manifest({ height: 6 }), 95, LIMITS)).toBe(
      "This folder goes 101 levels deep here. Kaagaz keeps at most 100 levels. Choose a place nearer the top.",
    );
    expect(dialogProblem(manifest({ height: 5 }), 95, LIMITS)).toBeNull();
  });

  it("has a folder name Kaagaz can't keep", () => {
    const long = "x".repeat(101);
    expect(dialogProblem(manifest({ badName: { name: long, problem: "long" } }), 0, LIMITS)).toBe(
      `A folder name is longer than 100 characters: “${long}”. Rename it, then choose the folder again.`,
    );
    expect(dialogProblem(manifest({ badName: { name: "Bills​", problem: "hidden" } }), 0, LIMITS)).toBe(
      "A folder name has a hidden character in it: “Bills”. Rename it, then choose the folder again.",
    );
  });

  it("is over this organisation's limits, each named in words", () => {
    expect(dialogProblem(manifest({ fileCount: 60000 }), 0, LIMITS)).toBe(
      "A folder upload can hold at most 50,000 files. This one has 60,000.",
    );
    expect(dialogProblem(manifest({ totalBytes: 7.2 * 1024 ** 3 }), 0, LIMITS)).toBe(
      "A folder upload can be at most 5 GB. This one is 7.2 GB.",
    );
    const many = Array.from({ length: 10001 }, (_, n) => ({ name: `F${n}`, parent: n === 0 ? null : 0 }));
    expect(dialogProblem(manifest({ folders: many }), 0, LIMITS)).toBe(
      "A folder upload can make at most 10,000 folders. This one has 10,001.",
    );
    expect(dialogProblem(manifest({}), 0, undefined)).toBeNull(); // an older server: it checks itself
  });
});

describe("a same-name folder already there", () => {
  const tree = buildTree([
    folder("acc", "Accounts", null, "department"),
    folder("old", "Old scans"),
    folder("inv", "INVOICES", "acc"),
    folder("inv2", "Invoices (2)", "acc"),
  ]);

  it("is found in any letter case, and Keep separate takes the first free (n)", () => {
    expect(clashOf(tree, { kind: "folder", parentId: "acc" }, "Invoices")).toEqual({
      merge: true,
      existing: "INVOICES",
      separate: "Invoices (3)",
    });
    expect(clashOf(tree, { kind: "folder", parentId: "acc" }, "Receipts")).toBeNull();
  });

  it("as a new department, merges only into a department; a plain folder of that name makes (2)", () => {
    expect(clashOf(tree, { kind: "department" }, "accounts")).toMatchObject({ merge: true, existing: "Accounts" });
    expect(clashOf(tree, { kind: "department" }, "Old scans")).toEqual({ merge: false, existing: "Old scans", separate: "Old scans (2)" });
  });
});

describe("the dialog", () => {
  const pick = fromFileList([picked("A/B/one.pdf", 1024), picked("A/two.pdf", 1024), picked("A/.DS_Store")]);
  if (!pick) throw new Error("empty");
  const source = { picked: pick, manifest: buildManifest(pick) };
  const tree = buildTree([folder("acc", "Accounts", null, "department")]);
  const dialog = (admin: boolean) =>
    renderToStaticMarkup(
      createElement(FolderUploadDialog, {
        source,
        tree,
        folderId: null,
        admin,
        limits: LIMITS,
        onStart: () => undefined,
        onClose: () => undefined,
      }),
    );

  it("says what was read, what was skipped, and how long reading takes", () => {
    expect(summaryLines(source)).toEqual({
      counts: "2 folders, 2 files, 2 KB. 2 levels deep.",
      skipped: "1 hidden or system file skipped",
      read: "Kaagaz will read 2 files. Reading can take a while for big folders.",
    });
    const html = dialog(true);
    expect(html).toContain("Upload “A”");
    expect(html).toContain("Kaagaz keeps its folders: every file goes into the same folder it is in now.");
    expect(html).toContain("A/.DS_Store");
    expect(html).toContain("This browser can’t see empty folders, so they won’t be made. Drag the folder here instead to keep them.");
    expect(html).toContain("Start upload");
  });

  it("offers As a new department to an admin only", () => {
    expect(dialog(true)).toContain("As a new department");
    expect(dialog(false)).not.toContain("As a new department");
    expect(dialog(false)).toContain("Inside a folder");
  });
});

describe("the tray's row for a folder upload", () => {
  const row = (phase: "sending" | "needs_pick" | "stopped", held = true) => {
    let state = folderUploadsReducer(EMPTY_FOLDER_UPLOADS, { type: "start", id: "u", rootName: "A", total: 5000 });
    state = folderUploadsReducer(state, { type: "phase", id: "u", phase });
    for (let n = 0; n < 1240; n++) state = folderUploadsReducer(state, { type: "sent", id: "u", itemId: `i${n}`, outcome: "stored" });
    return renderToStaticMarkup(
      createElement(FolderUploadRow, {
        view: state.u!,
        held,
        notSent: { rows: [], total: 0 },
        tree: buildTree([]),
        limits: { pdf_bytes: 1, image_bytes: 1, sheet_bytes: 1, max_pages: 1, readers: [] },
        actions: {
          stop: () => undefined,
          resume: () => undefined,
          chooseAgain: () => undefined,
          retry: () => undefined,
          retryAll: () => undefined,
          clear: () => undefined,
          loadItems: async () => ({ items: [], next_after: null }),
        },
      }),
    );
  };

  it("shows the count on its bar in words, and Stop while it sends", () => {
    const html = row("sending");
    expect(html).toContain('aria-valuetext="1,240 of 5,000"');
    expect(html).toContain("Folder “A”: 1,240 of 5,000 uploaded");
    expect(html).toContain(">Stop<");
  });

  it("after a reload names the folder to choose again; stopped here, it continues with the files it holds", () => {
    expect(row("needs_pick", false)).toContain("Choose “A” again");
    expect(row("stopped", true)).toContain("Continue upload");
    expect(row("stopped", false)).toContain("Choose “A” again");
  });
});

describe("the activity words", () => {
  it("says a folder upload with its count, never its names", () => {
    expect(describeAction("folder.upload", { files: 5000, folders_created: 312 })).toBe("Uploaded a folder");
    expect(activityWords({ action: "folder.upload", actor_name: "Priya", details: { files: 5000 } })).toBe(
      "uploaded a folder of 5,000 files",
    );
  });
});

describe("where a folder upload starts", () => {
  const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

  it("is Upload a folder on the Documents screen, and a folder dropped there, into the folder shown", () => {
    const view = read("components/documents/DocumentsView.tsx");
    expect(view).toContain("onClick={() => folderPicking.pick(library.folderId)}");
    expect(view.match(/onFolders=\{dropFolders\}/g)).toHaveLength(2);
    expect(view).toContain("folderPicking.drop(entries, library.folderId)");
  });

  it("offers Let Kaagaz sort files only when auto-filing would sort them, for loose files with no folder", () => {
    const view = read("components/documents/DocumentsView.tsx");
    expect(view).toMatch(/\{uploads\.limits\.sort_available && \(\s*<button[\s\S]*?Let Kaagaz sort files/);
    expect(view).toMatch(/function onChooseToSort[\s\S]*?void uploads\.addFiles\(files\);\n {2}\}/);
  });

  it("picks a whole folder in the shell, so it keeps going between screens", () => {
    const shell = read("components/shell/Uploads.tsx");
    expect(shell).toContain("element.webkitdirectory = true");
    expect(shell).toContain("<FolderUploadRow");
  });
});
