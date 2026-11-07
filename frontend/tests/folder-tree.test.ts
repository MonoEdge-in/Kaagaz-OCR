import { describe, expect, it } from "vitest";
import {
  CHILD_CAP,
  MAX_FOLDER_DEPTH,
  buildTree,
  canAddSubfolder,
  collapseCrumbs,
  folderPath,
  inTreeOrder,
  pathLabel,
  searchFolders,
  shortPathLabel,
  validMoveTargets,
  visibleNodes,
} from "@/components/documents/folderTree";
import type { FolderNode, VisibleRow } from "@/components/documents/folderTree";
import type { Folder } from "@/lib/types";

const folder = (id: string, name: string, parent_id: string | null = null): Folder => ({
  id,
  name,
  parent_id,
  created_at: "2026-09-29T10:00:00+05:30",
});

/** The tree as nested names, so a failure reads like a picture of the tree. */
type Shape = string | [string, Shape[]];
const shape = (nodes: readonly FolderNode[]): Shape[] =>
  nodes.map((node) =>
    node.children.length > 0 ? [node.folder.name, shape(node.children)] : node.folder.name,
  );
const names = (nodes: readonly FolderNode[]) => nodes.map((node) => node.folder.name);

describe("buildTree", () => {
  it("nests folders under their parents, with depth 1 at the top", () => {
    const tree = buildTree([
      folder("q", "Q1", "y"),
      folder("s", "Suppliers"),
      folder("y", "2026", "s"),
      folder("b", "Bank"),
    ]);
    expect(shape(tree.roots)).toEqual(["Bank", ["Suppliers", [["2026", ["Q1"]]]]]);
    expect(tree.byId.get("s")?.depth).toBe(1);
    expect(tree.byId.get("q")?.depth).toBe(3);
    expect(tree.byId.get("q")?.parent?.folder.name).toBe("2026");
  });

  it("sorts folders at the same level by name, whatever the letter case", () => {
    const tree = buildTree([
      folder("1", "cash"),
      folder("2", "Bills"),
      folder("3", "apple"),
      folder("4", "Bank"),
    ]);
    expect(names(tree.roots)).toEqual(["apple", "Bank", "Bills", "cash"]);
  });

  it("puts a folder whose parent is missing (an orphan) at the top level", () => {
    const tree = buildTree([folder("x", "Lost", "gone"), folder("a", "Accounts")]);
    expect(shape(tree.roots)).toEqual(["Accounts", "Lost"]);
    expect(tree.byId.get("x")?.depth).toBe(1);
  });

  it("never loops on bad input: a folder that is its own parent, or two that are each other's", () => {
    const tree = buildTree([
      folder("s", "Self", "s"),
      folder("a", "A", "b"),
      folder("b", "B", "a"),
      folder("c", "Below the loop", "a"),
      folder("a", "A again"),
    ]);
    const ids = inTreeOrder(tree).map((node) => node.folder.id);
    expect([...ids].sort()).toEqual(["a", "b", "c", "s"]);
    expect(tree.byId.get("s")?.depth).toBe(1);
    for (const node of inTreeOrder(tree)) expect(node.depth).toBeLessThanOrEqual(3);
  });

  it("gives an empty tree for no folders", () => {
    const tree = buildTree([]);
    expect(tree.roots).toEqual([]);
    expect(inTreeOrder(tree)).toEqual([]);
  });
});

describe("folderPath", () => {
  const tree = buildTree([folder("s", "Suppliers"), folder("y", "2026", "s")]);

  it('reads "Suppliers / 2026", top first', () => {
    expect(folderPath(tree, "y").map((f) => f.name)).toEqual(["Suppliers", "2026"]);
    expect(pathLabel(tree, "y")).toBe("Suppliers / 2026");
    expect(pathLabel(tree, "s")).toBe("Suppliers");
  });

  it("is empty for a folder that is not there", () => {
    expect(folderPath(tree, "nope")).toEqual([]);
    expect(pathLabel(tree, "nope")).toBe("");
    expect(tree.byId.has("nope")).toBe(false);
  });
});

// Changed on purpose (#484, Tushar 3 Nov 2026): folders nest up to 100 levels, Google Drive's
// own maximum, so an uploaded folder keeps its whole structure.
/** A chain of `levels` folders, L1 at the top, each inside the one before. */
const chain = (levels: number, prefix = "L") =>
  Array.from({ length: levels }, (_, index) =>
    folder(`${prefix}${index + 1}`, `${prefix}${index + 1}`, index === 0 ? null : `${prefix}${index}`),
  );

describe("validMoveTargets", () => {
  // A > A1 > A11 (3 levels); L1 > … > L98; C.
  const tree = buildTree([
    folder("A", "A"),
    folder("A1", "A1", "A"),
    folder("A11", "A11", "A1"),
    ...chain(98),
    folder("C", "C"),
  ]);

  it("leaves out the folder itself and everything inside it", () => {
    const targets = names(validMoveTargets(tree, "A"));
    expect(targets).not.toContain("A");
    expect(targets).not.toContain("A1");
    expect(targets).not.toContain("A11");
  });

  it("keeps a 3-level folder within 100 levels", () => {
    expect(MAX_FOLDER_DEPTH).toBe(100);
    // A holds 3 levels, so it fits under L1 to L97, never under L98.
    const targets = names(validMoveTargets(tree, "A"));
    expect(targets).toContain("L97");
    expect(targets).not.toContain("L98");
    expect(targets).toContain("C");
  });

  it("lets what is inside a folder go one level deeper than the folder itself (#537)", () => {
    // A's two levels inside it land just below the target: under L98 they reach level 100.
    const targets = names(validMoveTargets(tree, "A", true));
    expect(targets).toContain("L98");
    expect(targets).not.toContain("A1");
  });

  it("lets a single folder go under any folder above the 100th level, in tree order", () => {
    expect(names(validMoveTargets(tree, "A11")).slice(0, 4)).toEqual(["A", "A1", "C", "L1"]);
    expect(names(validMoveTargets(tree, "A11"))).toContain("L98");
  });

  it("has no targets for a folder that is not there", () => {
    expect(validMoveTargets(tree, "nope")).toEqual([]);
  });

  it("allows a new folder at level 99, never inside level 100", () => {
    const deep = buildTree(chain(100));
    expect(canAddSubfolder(deep, null)).toBe(true);
    expect(canAddSubfolder(deep, "L99")).toBe(true);
    expect(canAddSubfolder(deep, "L100")).toBe(false);
  });
});

describe("visibleNodes", () => {
  // 50 top-level folders with 100 each: 5,050 folders.
  const big = buildTree(
    Array.from({ length: 50 }, (_, top) => [
      folder(`T${top}`, `Top ${top}`),
      ...Array.from({ length: 100 }, (_, child) => folder(`T${top}-${child}`, `Child ${child}`, `T${top}`)),
    ]).flat(),
  );
  const folderRows = (rows: VisibleRow[]) => rows.flatMap((row) => (row.kind === "folder" ? [row] : []));

  it("shows only the top level when nothing is open", () => {
    const shown = folderRows(visibleNodes(big.roots, new Set(), null));
    expect(shown).toHaveLength(50);
    expect(shown.every((each) => !each.open && each.hasChildren)).toBe(true);
  });

  it("opens the folders above the one shown, and that folder itself", () => {
    const tree = buildTree(chain(30));
    const shown = folderRows(visibleNodes(tree.roots, new Set(), tree.byId.get("L10") ?? null));
    expect(shown.map((each) => each.node.folder.name)).toEqual(chain(11).map((each) => each.name));
    expect(shown[9]?.open).toBe(true); // L10 shows what is inside it
    expect(shown[10]?.open).toBe(false);
  });

  it("opens a folder the person opened, and keeps a closed one closed", () => {
    const shown = folderRows(visibleNodes(big.roots, new Set(["T3"]), null));
    expect(shown).toHaveLength(150);
    expect(shown[3]?.open).toBe(true);
    expect(shown[4]?.node.folder.id).toBe("T3-0");
  });

  it("shows at most 200 of one folder's folders, then Show all for the rest", () => {
    const wide = buildTree([folder("W", "Wide"), ...Array.from({ length: 450 }, (_, n) => folder(`W${n}`, `F${n}`, "W"))]);
    const capped = visibleNodes(wide.roots, new Set(["W"]), null);
    expect(CHILD_CAP).toBe(200);
    expect(folderRows(capped)).toHaveLength(1 + CHILD_CAP);
    expect(capped.at(-1)).toMatchObject({ kind: "more", hidden: 250 });
    const all = visibleNodes(wide.roots, new Set(["W"]), null, new Set(["W"]));
    expect(all).toHaveLength(451);
    expect(all.some((row) => row.kind === "more")).toBe(false);
  });
});

describe("collapseCrumbs", () => {
  it("keeps the first and the last two of 30 and hides 27", () => {
    const { head, hidden, tail } = collapseCrumbs(Array.from({ length: 30 }, (_, n) => n));
    expect(head).toEqual([0]);
    expect(hidden).toHaveLength(27);
    expect(tail).toEqual([28, 29]);
  });

  it("hides nothing in a short path", () => {
    expect(collapseCrumbs([1, 2, 3, 4])).toEqual({ head: [1, 2, 3, 4], hidden: [], tail: [] });
  });
});

describe("shortPathLabel", () => {
  const tree = buildTree(chain(40));

  it("reads 'L1 / … / L39 / L40' 40 levels deep", () => {
    expect(shortPathLabel(tree, "L40")).toBe("L1 / … / L39 / L40");
  });

  it("is the whole path when it is short", () => {
    expect(shortPathLabel(tree, "L3")).toBe("L1 / L2 / L3");
    expect(shortPathLabel(tree, "nope")).toBe("");
  });
});

describe("searchFolders", () => {
  const tree = buildTree([
    folder("s", "Suppliers"),
    ...Array.from({ length: 80 }, (_, n) => folder(`b${n}`, `Bills ${n}`, "s")),
  ]);

  it("finds names that hold the words, in any letter case, in tree order", () => {
    expect(names(searchFolders(tree, "SUPP"))).toEqual(["Suppliers"]);
    expect(names(searchFolders(tree, "bills 7"))[0]).toBe("Bills 7");
  });

  it("stops at 50, and finds nothing for an empty search", () => {
    expect(searchFolders(tree, "bills")).toHaveLength(50);
    expect(searchFolders(tree, "  ")).toEqual([]);
  });
});
