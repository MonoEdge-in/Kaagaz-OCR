// PRD 7 PR 11b (#604): the people icon means only Department only and Private. It shows after the
// folder's name in the tree, the tiles and the Move picker, read as words; the place heading says
// who sees the folder's files. A folder inside a private one has no icon of its own.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { placeHint, placeKind, privateFolderOf, privateRefusal } from "@/components/access/places";
import { FolderNav } from "@/components/documents/FolderNav";
import { FolderPicker } from "@/components/documents/FolderPicker";
import { buildTree } from "@/components/documents/folderTree";
import { ALL_DOCUMENTS } from "@/components/documents/libraryQuery";
import { FolderTiles } from "@/components/files/FolderTiles";
import type { Folder } from "@/lib/types";

const folder = (id: string, name: string, parent_id: string | null, more: Partial<Folder> = {}): Folder => ({
  id,
  name,
  parent_id,
  created_at: "2026-11-01T00:00:00Z",
  ...more,
});
const FOLDERS = [
  folder("fin", "Finance", null, { kind: "department", department_only: true }),
  folder("sal", "Salaries", "fin", { private: true }),
  folder("y26", "2026", "sal"),
  folder("tax", "Tax", "fin"),
  folder("hr", "HR", null, { kind: "department" }),
  folder("old", "Old scans", null, { private: true }),
  folder("misc", "Misc", null, { department_only: true }), // not a department: the flag means nothing
];
const TREE = buildTree(FOLDERS);
const nothing = () => undefined;

describe("which folders get the people icon", () => {
  it("is a private folder, or a department kept to its people; nothing else", () => {
    const kind = (id: string) => placeKind(TREE.byId.get(id)!.folder);
    expect(kind("fin")).toBe("department_only");
    expect(kind("sal")).toBe("private");
    expect(kind("old")).toBe("private");
    expect([kind("y26"), kind("tax"), kind("hr"), kind("misc")]).toEqual([null, null, null, null]);
  });

  it("finds the private folder a folder is in: itself or the nearest above", () => {
    expect(privateFolderOf(TREE, "y26")?.name).toBe("Salaries");
    expect(privateFolderOf(TREE, "sal")?.name).toBe("Salaries");
    expect(privateFolderOf(TREE, "tax")).toBeNull();
  });
});

describe("the place heading's words", () => {
  it("says who sees each kind of place, and nothing for an open folder", () => {
    expect(placeHint(TREE, "fin")).toBe(
      "Only Finance's people, its head, Management and people given a file directly can see these files.",
    );
    expect(placeHint(TREE, "sal")).toBe("Only the people named on it, the head of Finance and Management can see this folder.");
    expect(placeHint(TREE, "old")).toBe("Only the people named on it and Management can see this folder.");
    expect(placeHint(TREE, "y26")).toBe("Inside the private folder “Salaries”.");
    expect(placeHint(TREE, "tax")).toBeNull();
    expect(placeHint(TREE, "hr")).toBeNull();
    expect(placeHint(TREE, "gone")).toBeNull();
  });
});

describe("why Make private is refused before asking", () => {
  it("gives the server's reason for a department, a folder inside a private one, or one holding one", () => {
    expect(privateRefusal(TREE, "hr")).toBe("department");
    expect(privateRefusal(TREE, "y26")).toBe("inside_private");
    expect(privateRefusal(TREE, "fin")).toBe("department");
    const open = buildTree([folder("a", "Accounts", null), folder("b", "2025", "a"), folder("c", "Q1", "b", { private: true })]);
    expect(privateRefusal(open, "a")).toBe("holds_private");
    expect(privateRefusal(open, "b")).toBe("holds_private");
    expect(privateRefusal(TREE, "tax")).toBeNull();
    expect(privateRefusal(TREE, "sal")).toBeNull(); // private already: it is made open again instead
  });
});

describe("the icon in the tree, the tiles and the Move picker", () => {
  const nav = renderToStaticMarkup(
    createElement(FolderNav, {
      tree: TREE,
      library: { ...ALL_DOCUMENTS, folderId: "y26" }, // the way to it is open
      loading: false,
      error: null,
      onRetry: nothing,
      onNavigate: nothing,
    }),
  );
  const row = (name: string) => {
    const start = nav.indexOf(`>${name}</span>`);
    return nav.slice(start, nav.indexOf("</li>", start));
  };

  it("follows a Department only or Private name, titled, with its words for screen readers", () => {
    expect(row("Finance")).toContain('title="Department only"');
    expect(row("Finance")).toContain('<span class="visually-hidden">, department only</span>');
    expect(row("Salaries")).toContain('title="Private"');
    expect(row("Salaries")).toContain('<span class="visually-hidden">, private</span>');
    expect(row("Salaries")).toContain('<circle cx="9" cy="8" r="3.8">');
  });

  it("is on no other folder: a plain one, a department open to everyone, one inside a private folder", () => {
    for (const name of ["2026", "Tax", "HR", "Misc"]) {
      expect(row(name), name).not.toMatch(/, (private|department only)</);
      expect(row(name), name).not.toContain('r="3.8"');
    }
  });

  it("is on the tiles and in the Move picker too", () => {
    const tiles = renderToStaticMarkup(
      createElement(FolderTiles, { folders: TREE.byId.get("fin")!.children, library: ALL_DOCUMENTS, counts: {} }),
    );
    expect(tiles.match(/, private</g)).toHaveLength(1);
    expect(tiles.slice(tiles.indexOf(">Tax"))).not.toContain(", private");
    const picker = renderToStaticMarkup(
      createElement(FolderPicker, { tree: TREE, name: "to", choice: "y26", onChoose: nothing }),
    );
    expect(picker).toContain(", department only");
    expect(picker.match(/, private</g)).toHaveLength(2); // Salaries and Old scans
  });
});
