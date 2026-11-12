// Folders 100 levels deep (#484): the tree draws only what is open, at most 200 of one
// folder's folders, marks each level for screen readers and in words past the 8-level indent,
// and the folder path folds its middle away.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { FolderCrumbs, FolderNav, FolderPath, INDENT_LEVELS } from "@/components/documents/FolderNav";
import { FolderPicker } from "@/components/documents/FolderPicker";
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
    // Changed on purpose (#572): the indent stops growing after 6 levels, so the level is said in words there.
    expect(html).toContain("--depth:6");
    expect(html).not.toContain("--depth:7");
    expect(html).toMatch(/>L30<\/span><span class="[^"]*treeLevel[^"]*">Level 30<\/span>/);
    expect(html).not.toMatch(/<span class="(?!visually-hidden)[^"]*">Level 6</); // said, not shown
  });

  it("keeps a deep folder's name on one line, its level on a line below, and its path on hover (#572)", () => {
    expect(INDENT_LEVELS).toBe(6);
    const html = nav(buildTree(chain(30)), "L30");
    const path = Array.from({ length: 30 }, (_, n) => `L${n + 1}`).join(" / ");
    // Past the indent, the name's own span holds the whole path for the pointer, then the level in words.
    expect(html).toContain(`title="${path}">L30</span>`);
    expect(html).toMatch(/<span class="[^"]*treeName[^"]*" title="L1 \/ L2 \/ [^"]*L30">L30<\/span>/);
    // Within the indent the name is its own title, so a cut name can still be read whole.
    expect(html).toMatch(/<span class="[^"]*treeName[^"]*" title="L2">L2<\/span>/);
  });

  it("never lets the folder tree scroll sideways or break a name between letters (#572)", () => {
    const css = readFileSync("components/documents/Library.module.css", "utf8");
    const rule = (selector: string) => {
      const at = css.indexOf(`\n${selector} {`);
      return at < 0 ? "" : css.slice(at, css.indexOf("}", at));
    };
    expect(rule(".treeName")).toMatch(/white-space: nowrap;/);
    expect(rule(".treeName")).toMatch(/overflow: hidden;/);
    expect(rule(".treeName")).toMatch(/text-overflow: ellipsis;/);
    expect(rule(".treeText")).toMatch(/min-width: 0;/);
    expect(rule(".treeRow")).toMatch(/var\(--depth, 0\) \* var\(--space-2\)/);
    expect(css).toMatch(/\.treePane \{[^}]*overflow-x: hidden;/);
    expect(rule(".treeLink")).not.toMatch(/overflow-wrap: anywhere/);
  });

  it("says each level past the first to a screen reader, in the tree and in the folder picker", () => {
    const picker = renderToStaticMarkup(
      createElement(FolderPicker, { tree: buildTree(chain(3)), name: "p", choice: "L3", onChoose: () => undefined }),
    );
    for (const html of [nav(buildTree(chain(3)), "L3"), picker]) {
      expect(html).toMatch(/L2<\/span><span class="visually-hidden">, Level 2<\/span>/);
      expect(html).toMatch(/L3<\/span><span class="visually-hidden">, Level 3<\/span>/);
      expect(html).not.toContain("Level 1<");
    }
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
    // Changed on purpose (#572): a button, not a fold-out list, and the folded folders are not drawn.
    expect(html).toMatch(/<button[^>]*aria-expanded="false"[^>]*>Show 26 more folders<\/button>/);
    expect(html).not.toContain(">L5<");
    expect(html).not.toContain(">L30<"); // the place itself is the heading below the path
  });

  it("folds nothing in a short path", () => {
    expect(crumbs("L3")).not.toContain("more folders");
  });

  it("opened, puts the folded folders back in line in the same path, with Show fewer at its end (#572)", () => {
    const html = renderToStaticMarkup(
      createElement(FolderPath, { tree, library: ALL_DOCUMENTS, folderId: "L30", open: true, onToggle: () => undefined }),
    );
    expect(html.match(/<ol/g)).toHaveLength(1); // one wrapping line of links, never a column inside it
    expect(html).not.toContain("<details");
    expect(html).toMatch(/>L1<[\s\S]*>L2<[\s\S]*>L27<[\s\S]*>L28<[\s\S]*>L29</);
    expect(html).toMatch(/<button[^>]*aria-expanded="true"[^>]*>Show fewer<\/button><\/li><\/ol>/);
  });

  it("draws no column and no stray slash for the folded folders (#572)", () => {
    const css = readFileSync("components/documents/Library.module.css", "utf8");
    expect(css).not.toContain(".crumbsMore");
    expect(css).toMatch(/\.crumbs li\.crumbsEnd::before \{[^}]*content: none;/);
  });
});
