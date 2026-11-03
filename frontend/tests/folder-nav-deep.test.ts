// Folders 100 levels deep (#484): the tree draws only what is open, at most 200 of one
// folder's folders, marks each level for screen readers and in words past the 8-level indent,
// and the folder path folds its middle away.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FolderCrumbs, FolderNav } from "@/components/documents/FolderNav";
import { buildTree } from "@/components/documents/folderTree";
import type { FolderTree } from "@/components/documents/folderTree";
import { ALL_DOCUMENTS } from "@/components/documents/libraryQuery";
import type { Folder } from "@/lib/types";

const folder = (id: string, name: string, parent_id: string | null = null): Folder => ({
  id,
  name,
  parent_id,
  created_at: "2026-11-03T10:00:00Z",
});
const chain = (levels: number) =>
  Array.from({ length: levels }, (_, n) => folder(`L${n + 1}`, `L${n + 1}`, n === 0 ? null : `L${n}`));

const nav = (tree: FolderTree, folderId: string | null = null) =>
  renderToStaticMarkup(
    createElement(FolderNav, {
      tree,
      library: { ...ALL_DOCUMENTS, folderId },
      loading: false,
      error: null,
      onRetry: () => undefined,
      onNavigate: () => undefined,
    }),
  );

describe("the folder tree, deep and wide", () => {
  it("draws only the top level of a 5,050-folder tree, each with a button to show what is inside", () => {
    const tree = buildTree(
      Array.from({ length: 50 }, (_, top) => [
        folder(`T${top}`, `Top ${top}`),
        ...Array.from({ length: 100 }, (_, child) => folder(`T${top}-${child}`, `Child ${child}`, `T${top}`)),
      ]).flat(),
    );
    const html = nav(tree);
    expect(html).not.toContain("Child 0");
    expect(html.match(/aria-expanded="false"/g)).toHaveLength(50);
    expect(html).toContain('aria-label="Show folders in Top 7"');
  });

  it("opens the path to the folder shown, and marks every level", () => {
    const html = nav(buildTree(chain(30)), "L30");
    expect(html).toContain('aria-level="30"');
    expect(html).toContain('aria-label="Hide folders in L29"');
    // The indent stops growing after 8 levels, so the level is said in words there.
    expect(html).toContain("--depth:8");
    expect(html).not.toContain("--depth:9");
    expect(html).toMatch(/L30<span[^>]*>Level 30<\/span>/);
    expect(html).not.toContain("Level 8<");
  });

  it("shows 200 of one folder's folders, with Show all for the rest, and offers a search", () => {
    const wide = buildTree([folder("W", "Wide"), ...Array.from({ length: 450 }, (_, n) => folder(`W${n}`, `F${n}`, "W"))]);
    const html = nav(wide, "W");
    expect(html.match(/aria-level="2"/g)).toHaveLength(200);
    expect(html).toContain("Show all 450 folders in Wide");
    expect(html).toContain("Find a folder");
    expect(nav(buildTree(chain(3)))).not.toContain("Find a folder");
  });
});

describe("the folder path", () => {
  const tree = buildTree(chain(30));
  const crumbs = (id: string) =>
    renderToStaticMarkup(createElement(FolderCrumbs, { tree, library: ALL_DOCUMENTS, folderId: id }));

  it("keeps All documents, the first folder and the last two above, and folds the 26 between", () => {
    const html = crumbs("L30");
    expect(html).toContain("All documents");
    expect(html).toContain(">L1<");
    expect(html).toContain(">L28<");
    expect(html).toContain(">L29<");
    expect(html).toContain("<summary");
    expect(html).toContain("Show 26 more folders");
    expect(html).not.toContain(">L30<"); // the place itself is the heading below the path
  });

  it("folds nothing in a short path", () => {
    expect(crumbs("L3")).not.toContain("<summary");
  });
});
