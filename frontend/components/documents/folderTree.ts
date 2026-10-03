// The folder tree, built from the flat list GET /api/folders sends (each
// folder names its parent). Pure functions, unit tested in node
// (tests/folder-tree.test.ts). The server enforces the real rules (no loops,
// at most 5 levels, names unique among siblings); these only decide what the
// screen offers, and they never loop, even on bad input.

import type { Folder } from "@/lib/types";

/** Folders nest at most this deep. A top-level folder is at depth 1 (PRD 1, F1). */
export const MAX_FOLDER_DEPTH = 5;

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
 * and only where its deepest folder stays within 5 levels. The top level is
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
