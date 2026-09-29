import { describe, expect, it } from "vitest";
import {
  MAX_FOLDER_DEPTH,
  buildTree,
  canAddSubfolder,
  depthOf,
  folderPath,
  inTreeOrder,
  pathLabel,
  validMoveTargets,
} from "@/components/documents/folderTree";
import type { FolderNode } from "@/components/documents/folderTree";
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
    expect(depthOf(tree, "s")).toBe(1);
    expect(depthOf(tree, "q")).toBe(3);
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
    expect(depthOf(tree, "x")).toBe(1);
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
    expect(depthOf(tree, "s")).toBe(1);
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
    expect(depthOf(tree, "nope")).toBe(0);
    expect(depthOf(tree, null)).toBe(0);
  });
});

describe("validMoveTargets", () => {
  // A > A1 > A11; B > B1 > B11 > B111 > B1111 (5 deep); C.
  const tree = buildTree([
    folder("A", "A"),
    folder("A1", "A1", "A"),
    folder("A11", "A11", "A1"),
    folder("B", "B"),
    folder("B1", "B1", "B"),
    folder("B11", "B11", "B1"),
    folder("B111", "B111", "B11"),
    folder("B1111", "B1111", "B111"),
    folder("C", "C"),
  ]);

  it("leaves out the folder itself and everything inside it", () => {
    const targets = names(validMoveTargets(tree, "A"));
    expect(targets).not.toContain("A");
    expect(targets).not.toContain("A1");
    expect(targets).not.toContain("A11");
  });

  it("leaves out targets that would make the tree deeper than 5 levels", () => {
    expect(MAX_FOLDER_DEPTH).toBe(5);
    // A holds 3 levels, so it fits only under a folder at depth 1 or 2.
    expect(names(validMoveTargets(tree, "A"))).toEqual(["B", "B1", "C"]);
    // B1 holds 4 levels, so only under a top-level folder.
    expect(names(validMoveTargets(tree, "B1"))).toEqual(["A", "B", "C"]);
  });

  it("lists every other folder for a single folder, in tree order, except the 5th level", () => {
    expect(names(validMoveTargets(tree, "A11"))).toEqual([
      "A",
      "A1",
      "B",
      "B1",
      "B11",
      "B111",
      "C",
    ]);
  });

  it("has no targets for a folder that is not there", () => {
    expect(validMoveTargets(tree, "nope")).toEqual([]);
  });

  it("allows a new folder only above the 5th level", () => {
    expect(canAddSubfolder(tree, null)).toBe(true);
    expect(canAddSubfolder(tree, "B111")).toBe(true);
    expect(canAddSubfolder(tree, "B1111")).toBe(false);
  });
});
