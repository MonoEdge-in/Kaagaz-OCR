// Reading a picked or dropped folder (#484): the files and their paths, the folders (a dropped
// folder's empty ones too), hidden and system files skipped and listed, then the manifest the
// server plans from: folders parents first, each file's folder, the counts, the height, and the
// first folder name Kaagaz can't keep.

import { describe, expect, it } from "vitest";
import {
  buildManifest,
  droppedItems,
  fromFileList,
  isSkipped,
  nameProblem,
  noFolderPaths,
  pathKey,
  walkDrop,
} from "@/components/documents/folderPick";

/** A File with the path the browser gives a folder pick. */
function picked(path: string, size = 10): File {
  const file = new File([new Uint8Array(size)], path.split("/").at(-1) ?? path);
  Object.defineProperty(file, "webkitRelativePath", { value: path });
  return file;
}

// Fake FileSystemEntry objects: readEntries gives at most `batch` at a time, then [] (as Chrome).
type Fake = { name: string; isFile: boolean; isDirectory: boolean };
function fileEntry(name: string, size = 10): Fake & { file(ok: (file: File) => void): void } {
  return { name, isFile: true, isDirectory: false, file: (ok) => ok(new File([new Uint8Array(size)], name)) };
}
function dirEntry(name: string, children: Fake[], batch = 2): Fake & { createReader(): unknown } {
  return {
    name,
    isFile: false,
    isDirectory: true,
    createReader: () => {
      let at = 0;
      return {
        readEntries: (ok: (entries: Fake[]) => void) => {
          const next = children.slice(at, at + batch);
          at += next.length;
          queueMicrotask(() => ok(next));
        },
      };
    },
  };
}
const asDir = (fake: unknown) => fake as FileSystemDirectoryEntry;

describe("what is skipped", () => {
  it("skips .DS_Store, Thumbs.db, desktop.ini, ~$a.docx and .git, never a plain name", () => {
    for (const name of [".DS_Store", "Thumbs.db", "THUMBS.DB", "desktop.ini", "~$a.docx", ".git", "__MACOSX", "$RECYCLE.BIN"]) {
      expect(isSkipped(name), name).toBe(true);
    }
    for (const name of ["bill.pdf", "Invoices", "a.b.c.pdf", "~notes.pdf"]) expect(isSkipped(name), name).toBe(false);
  });

  it("from a folder pick: leaves out a hidden folder with its files, listed once, and counts it once", () => {
    const pick = fromFileList([
      picked("A/bill.pdf"),
      picked("A/.DS_Store"),
      picked("A/.git/config"),
      picked("A/.git/objects/x"),
      picked("A/B/scan.png"),
    ]);
    expect(pick?.rootName).toBe("A");
    expect(pick?.paths).toEqual(["A/bill.pdf", "A/B/scan.png"]);
    expect(pick?.skipped).toEqual(["A/.DS_Store", "A/.git"]);
    expect(pick?.skippedCount).toBe(2);
    expect(pick?.seesEmptyFolders).toBe(false);
  });
});

describe("a dropped folder", () => {
  it("walks every batch of entries, keeps empty folders, and skips hidden ones without going in", async () => {
    const counts: number[] = [];
    const root = dirEntry("A", [
      fileEntry("one.pdf"),
      fileEntry(".DS_Store"),
      dirEntry("Empty", []),
      dirEntry(".git", [fileEntry("config")]),
      dirEntry("B", [fileEntry("two.pdf"), fileEntry("three.pdf"), fileEntry("four.pdf")]),
    ]);
    const pick = await walkDrop(asDir(root), (files) => counts.push(files));
    expect(pick.paths).toEqual(["A/one.pdf", "A/B/two.pdf", "A/B/three.pdf", "A/B/four.pdf"]);
    expect(pick.folders).toEqual(["A", "A/Empty", "A/B"]);
    expect(pick.skipped).toEqual(["A/.DS_Store", "A/.git"]);
    expect(pick.seesEmptyFolders).toBe(true);
    expect(counts.at(-1)).toBe(4);
    expect(buildManifest(pick).folders.map((folder) => folder.name)).toContain("Empty");
  });

  it("splits a drop into its folders and its loose files, in the drop handler", () => {
    const loose = new File(["x"], "loose.pdf");
    const items = [
      { kind: "file", webkitGetAsEntry: () => dirEntry("A", []), getAsFile: () => null },
      { kind: "file", webkitGetAsEntry: () => dirEntry("B", []), getAsFile: () => null },
      { kind: "file", webkitGetAsEntry: () => fileEntry("loose.pdf"), getAsFile: () => loose },
      { kind: "string", webkitGetAsEntry: () => null, getAsFile: () => null },
    ];
    const { folders, files } = droppedItems(items as unknown as DataTransferItemList);
    expect(folders.map((entry) => entry.name)).toEqual(["A", "B"]);
    expect(files).toEqual([loose]);
  });
});

describe("the manifest", () => {
  it("lists parents first, gives each file its folder, and counts the height from the root", () => {
    const pick = fromFileList([picked("A/B/C/c.pdf", 5), picked("A/a.pdf", 7), picked("A/D/d.pdf", 1)]);
    if (!pick) throw new Error("no pick");
    const manifest = buildManifest(pick);
    expect(manifest.folders).toEqual([
      { name: "A", parent: null },
      { name: "B", parent: 0 },
      { name: "C", parent: 1 },
      { name: "D", parent: 0 },
    ]);
    expect([...manifest.fileFolder]).toEqual([2, 0, 3]);
    expect(manifest.fileCount).toBe(3);
    expect(manifest.totalBytes).toBe(13);
    expect(manifest.height).toBe(3);
    expect(manifest.badName).toBeNull();
  });

  it("finds a folder name longer than 100 characters, or one with a hidden control character", () => {
    const long = "x".repeat(101);
    const pick = fromFileList([picked(`A/${long}/a.pdf`)]);
    if (!pick) throw new Error("no pick");
    expect(buildManifest(pick).badName).toEqual({ name: long, problem: "long" });
    expect(nameProblem("Bills‮fdp")).toBe("hidden");
    expect(nameProblem("Bills​")).toBe("hidden");
    expect(nameProblem("..")).toBe("hidden");
    expect(nameProblem("Bills 2026")).toBeNull();
  });

  it("checks a name as the server does: Devanagari joiners pass; slashes, U+061C and hidden kinds don't", () => {
    expect(nameProblem("क्\u200Dष बिल")).toBeNull(); // ZWJ
    expect(nameProblem("क्\u200Cष")).toBeNull(); // ZWNJ
    for (const bad of ["a/b", "a\\b", "a\u061Cb", "a\tb", "a\u2028b", "a\u2029b", "a\uE000b", "a\u0378b", "a\uD800b", "a\uFEFFb"]) {
      expect(nameProblem(bad), JSON.stringify(bad)).toBe("hidden");
    }
  });

  it("knows a pick without folder paths: a phone's picker gives files only", () => {
    expect(noFolderPaths([new File(["x"], "a.pdf")])).toBe(true);
    expect(noFolderPaths([picked("A/a.pdf")])).toBe(false);
    expect(noFolderPaths([])).toBe(false);
  });

  it("re-planned from the same folder picked again, keeps the first plan's folders first, in order, and new ones after", () => {
    const first = fromFileList([picked("A/X/a.pdf"), picked("A/Y/b.pdf")]);
    const again = fromFileList([picked("A/Z/c.pdf"), picked("A/Y/b.pdf"), picked("A/X/W/d.pdf")]);
    if (!first || !again) throw new Error("empty");
    const earlier = buildManifest(first);
    const manifest = buildManifest(again, earlier);
    expect(manifest.folders.slice(0, earlier.folders.length)).toEqual(earlier.folders);
    expect(manifest.folders.slice(earlier.folders.length).map((folder) => folder.name)).toEqual(["Z", "W"]);
    const named = (name: string) => manifest.folders.findIndex((folder) => folder.name === name);
    expect(Array.from(manifest.fileFolder)).toEqual([named("Z"), named("Y"), named("W")]);
  });

  it("keeps names in one Unicode form, so a Mac's and a PC's copy are the same path", async () => {
    const mac = fromFileList([picked("A/Café/menu.pdf")]);
    expect(mac?.paths).toEqual(["A/Café/menu.pdf"]);
    expect(await pathKey("A/Café/menu.pdf")).toBe(await pathKey("A/Café/menu.pdf"));
    expect(await pathKey("A/a.pdf")).toMatch(/^[0-9a-f]{64}$/);
  });
});
