// A folder's access, from the folder list (PRD 7 PR 11b, #604): its people icon, the place
// heading's words, its department (for who may change it), and why Make private is refused before
// asking. Pure functions over the tree the person sees; tested in tests/folder-badges.test.ts.

import type { FolderTree } from "@/components/documents/folderTree";
import { departmentOf, isDepartment } from "@/components/files/departments";
import type { Folder } from "@/lib/types";
import { departmentOnlyHint, insidePrivateHint, privateHint } from "./words";

export type PlaceKind = "department_only" | "private";

/** The people icon's meaning (one meaning per icon): a private folder, or a department kept to its
 * people; null for every other folder (a folder inside a private one has none of its own). */
export function placeKind(folder: Pick<Folder, "kind" | "parent_id" | "department_only" | "private">): PlaceKind | null {
  if (folder.private) return "private";
  return isDepartment(folder) && folder.department_only ? "department_only" : null;
}

/** The private folder `folderId` is in: itself or the nearest above it; null for none. O(depth). */
export function privateFolderOf(tree: FolderTree, folderId: string): Folder | null {
  for (let node = tree.byId.get(folderId); node; node = node.parent ?? undefined) {
    if (node.folder.private) return node.folder;
  }
  return null;
}

/** The words under the place heading: who sees this folder's files; null for an open folder. */
export function placeHint(tree: FolderTree, folderId: string): string | null {
  const node = tree.byId.get(folderId);
  if (!node) return null;
  const kind = placeKind(node.folder);
  if (kind === "department_only") return departmentOnlyHint(node.folder.name);
  if (kind === "private") return privateHint(departmentOf(tree, folderId)?.name ?? null);
  const above = privateFolderOf(tree, folderId);
  return above ? insidePrivateHint(above.name) : null;
}

/** Why this folder can't be made private (the server's 409s, known from the tree an Admin sees);
 * null when it can. A private folder itself is made open again, never refused here. */
export function privateRefusal(tree: FolderTree, folderId: string): "department" | "inside_private" | "holds_private" | null {
  const node = tree.byId.get(folderId);
  if (!node || node.folder.private) return null;
  if (isDepartment(node.folder)) return "department";
  if (node.parent && privateFolderOf(tree, node.parent.folder.id)) return "inside_private";
  const below = [...node.children];
  for (let next = below.pop(); next; next = below.pop()) {
    if (next.folder.private) return "holds_private";
    below.push(...next.children);
  }
  return null;
}
