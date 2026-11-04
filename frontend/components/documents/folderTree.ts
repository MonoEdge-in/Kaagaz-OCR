// The folder tree, built from the flat list GET /api/folders sends (each
// folder names its parent). Pure functions, unit tested in node
// (tests/folder-tree.test.ts). The server enforces the real rules (no loops,
// at most 100 levels, names unique among siblings); these only decide what the
// screen offers, and they never loop, even on bad input. A customer may hold
// thousands of folders 100 levels deep (#484), so the screens draw only what is
// open (visibleNodes), at most CHILD_CAP of one folder's folders at a time.

import type { Folder } from "@/lib/types";

/** Folders nest at most this deep, Google Drive's own maximum (#484). A top-level folder is at depth 1. */
export const MAX_FOLDER_DEPTH = 100;

/** The most of one folder's folders drawn at once; "Show all" draws the rest (#484). */
export const CHILD_CAP = 200;

export interface FolderNode {
  folder: Folder;
  depth: number; // 1 at the top level
  parent: FolderNode | null; // null at the top level
  children: FolderNode[]; // sorted by name
}

export interface FolderTree {
  roots: FolderNode[]; // sorted by name
  byId: ReadonlyMap<string, FolderNode>;
}

// Names compare without letter case, and "Q2" comes before "Q10".
const names = new Intl.Collator("en-IN", { sensitivity: "base", numeric: true });

function byName(a: Folder, b: Folder): number {
  return names.compare(a.name, b.name) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
}

/**
 * The tree from the flat list. A folder whose parent is missing, or is the
 * folder itself, goes to the top level. Folders caught in a loop of parents
 * (which the server never allows) are put at the top level where the loop is
 * first met, so every folder shows exactly once.
 * Time O(F log F) for sorting the names (O(F) apart from that), space O(F).
 */
export function buildTree(folders: readonly Folder[]): FolderTree {
  const unique = new Map<string, Folder>();
  for (const folder of folders) {
    if (!unique.has(folder.id)) unique.set(folder.id, folder);
  }

  const childrenOf = new Map<string | null, Folder[]>();
  for (const folder of unique.values()) {
    const parent = folder.parent_id;
    const key = parent !== null && parent !== folder.id && unique.has(parent) ? parent : null;
    const siblings = childrenOf.get(key);
    if (siblings) siblings.push(folder);
    else childrenOf.set(key, [folder]);
  }
  for (const siblings of childrenOf.values()) siblings.sort(byName);

  // byId is also the "already placed" set, which is what stops a loop.
  const byId = new Map<string, FolderNode>();
  const roots: FolderNode[] = [];
  const plant = (folder: Folder) => {
    const root: FolderNode = { folder, depth: 1, parent: null, children: [] };
    byId.set(folder.id, root);
    roots.push(root);
    const queue = [root];
    // for...of also reaches the nodes pushed during the loop: breadth first.
    for (const node of queue) {
      for (const child of childrenOf.get(node.folder.id) ?? []) {
        if (byId.has(child.id)) continue;
        const next: FolderNode = { folder: child, depth: node.depth + 1, parent: node, children: [] };
        byId.set(child.id, next);
        node.children.push(next);
        queue.push(next);
      }
    }
  };

  for (const folder of childrenOf.get(null) ?? []) plant(folder);
  for (const folder of unique.values()) {
    if (!byId.has(folder.id)) plant(folder);
  }
  roots.sort((a, b) => byName(a.folder, b.folder));
  return { roots, byId };
}

/** Depth first, in the order the tree shows, leaving out one folder and all inside it. O(F). */
function preorder(roots: readonly FolderNode[], skipId: string | null): FolderNode[] {
  const out: FolderNode[] = [];
  const stack = roots.toReversed();
  for (let node = stack.pop(); node; node = stack.pop()) {
    if (node.folder.id === skipId) continue;
    out.push(node);
    stack.push(...node.children.toReversed());
  }
  return out;
}

/** Every folder, each followed by the folders inside it: the order the tree shows. O(F). */
export function inTreeOrder(tree: FolderTree): FolderNode[] {
  return preorder(tree.roots, null);
}

/** The folders from the top down to this one; [] for a folder not in the tree. O(depth). */
export function folderPath(tree: FolderTree, folderId: string): Folder[] {
  const path: Folder[] = [];
  for (let node = tree.byId.get(folderId); node; node = node.parent ?? undefined) {
    path.push(node.folder);
  }
  return path.reverse();
}

/** "Suppliers / 2026"; "" for a folder not in the tree. O(depth). */
export function pathLabel(tree: FolderTree, folderId: string): string {
  return folderPath(tree, folderId)
    .map((folder) => folder.name)
    .join(" / ");
}

/** Whether a new folder may go inside this one (null: at the top level). O(1). */
export function canAddSubfolder(tree: FolderTree, folderId: string | null): boolean {
  if (folderId === null) return true;
  const node = tree.byId.get(folderId);
  return node !== undefined && node.depth < MAX_FOLDER_DEPTH;
}

/** How many levels a folder holds, itself included: 1 when nothing is inside. O(size of it). */
function levelsIn(node: FolderNode): number {
  let deepest = node.depth;
  const stack = [node];
  for (let next = stack.pop(); next; next = stack.pop()) {
    deepest = Math.max(deepest, next.depth);
    stack.push(...next.children);
  }
  return deepest - node.depth + 1;
}

/**
 * Where a folder may move to: any folder except itself and those inside it,
 * and only where its deepest folder stays within MAX_FOLDER_DEPTH levels. The top level is
 * always allowed and is not in this list. In tree order. Time and space O(F).
 */
export function validMoveTargets(tree: FolderTree, folderId: string): FolderNode[] {
  const node = tree.byId.get(folderId);
  if (!node) return [];
  const levels = levelsIn(node);
  return preorder(tree.roots, folderId).filter(
    (target) => target.depth + levels <= MAX_FOLDER_DEPTH,
  );
}

/** One row of a tree drawn flat: a folder, or "Show all" for the folders of `parent` past CHILD_CAP. */
export type VisibleRow =
  | { kind: "folder"; node: FolderNode; open: boolean; hasChildren: boolean }
  | { kind: "more"; parent: FolderNode | null; depth: number; hidden: number };

/** The key in `showAll` for the top-level list itself. */
export const TOP_LIST = "";

/**
 * The rows to draw, top down: these folders, and inside each open one its folders. A folder
 * is open when it is in `expanded`, or is the one shown (`current`) or above it. Each list
 * stops at CHILD_CAP unless its parent's id (TOP_LIST for `nodes`) is in `showAll`.
 * O(rows drawn + depth of current).
 */
export function visibleNodes(
  nodes: readonly FolderNode[],
  expanded: ReadonlySet<string>,
  current: FolderNode | null,
  showAll: ReadonlySet<string> = new Set(),
): VisibleRow[] {
  const onPath = new Set<string>();
  for (let node = current; node; node = node.parent) onPath.add(node.folder.id);
  const rows: VisibleRow[] = [];
  const list = (siblings: readonly FolderNode[], parent: FolderNode | null) => {
    const shown = showAll.has(parent?.folder.id ?? TOP_LIST) ? siblings : siblings.slice(0, CHILD_CAP);
    for (const node of shown) {
      const hasChildren = node.children.length > 0;
      const open = hasChildren && (expanded.has(node.folder.id) || onPath.has(node.folder.id));
      rows.push({ kind: "folder", node, open, hasChildren });
      if (open) list(node.children, node);
    }
    if (shown.length < siblings.length) {
      rows.push({ kind: "more", parent, depth: (parent?.depth ?? 0) + 1, hidden: siblings.length - shown.length });
    }
  };
  list(nodes, null);
  return rows;
}

/** head + hidden + tail: the first `head` and last `tail`; hidden is empty when nothing would be. O(n). */
export function collapseCrumbs<T>(path: readonly T[], head = 1, tail = 2): { head: T[]; hidden: T[]; tail: T[] } {
  if (path.length <= head + tail + 1) return { head: [...path], hidden: [], tail: [] };
  return { head: path.slice(0, head), hidden: path.slice(head, path.length - tail), tail: path.slice(path.length - tail) };
}

/** "L1 / … / L39 / L40" past `keep` levels (the top and the last keep - 1); pathLabel when shorter. O(depth). */
export function shortPathLabel(tree: FolderTree, folderId: string, keep = 3): string {
  const names = folderPath(tree, folderId).map((folder) => folder.name);
  if (names.length <= keep) return names.join(" / ");
  return [names[0], "…", ...names.slice(names.length - (keep - 1))].join(" / ");
}

/** Folders whose name holds `query` (no letter case), in tree order, at most `limit`. O(F). */
export function searchFolders(tree: FolderTree, query: string, limit = 50): FolderNode[] {
  const words = query.trim().toLocaleLowerCase("en-IN");
  if (!words) return [];
  const found: FolderNode[] = [];
  for (const node of inTreeOrder(tree)) {
    if (node.folder.name.toLocaleLowerCase("en-IN").includes(words)) found.push(node);
    if (found.length === limit) break;
  }
  return found;
}
