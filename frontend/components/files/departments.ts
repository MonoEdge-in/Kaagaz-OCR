// Departments (File management F1, F2, F5): top-level folders of kind "department", in the
// admin's order. Pure functions, unit tested in node (tests/file-management.test.ts). PRD 9 F-01
// (D1): only a department picked is sent; the one last used is no longer kept or sent.

import type { FolderTree } from "@/components/documents/folderTree";
import type { Folder } from "@/lib/types";

export const isDepartment = (folder: Pick<Folder, "kind" | "parent_id">) =>
  folder.kind === "department" && folder.parent_id === null;

/** By sort_order (none last), then by name. O(F log F). */
export function departmentsOf(folders: readonly Folder[]): Folder[] {
  return folders
    .filter(isDepartment)
    .sort(
      (a, b) =>
        (a.sort_order ?? Number.MAX_SAFE_INTEGER) - (b.sort_order ?? Number.MAX_SAFE_INTEGER) ||
        a.name.localeCompare(b.name, "en-IN", { sensitivity: "base", numeric: true }),
    );
}

/** The department a folder sits in (itself, or its top folder when that is one); null otherwise. O(depth). */
export function departmentOf(tree: FolderTree, folderId: string | null): Folder | null {
  if (folderId === null) return null;
  let node = tree.byId.get(folderId);
  while (node?.parent) node = node.parent;
  return node && isDepartment(node.folder) ? node.folder : null;
}

/** B4: the department new files go into: the one picked while it is still one, else none (PRD 9
 * F-01: never the last one used). A department deleted since it was picked is not used. */
export function chosenDepartment(choice: string | null | undefined, departments: readonly Folder[]): string | null {
  return departments.some((department) => department.id === choice) ? (choice ?? null) : null;
}

/** PRD 9 F-02: where "Let Kaagaz sort files" sends files, to be filed inside: the department of
 * the place shown, or at All documents the one picked; none in a folder outside any department. */
export function sortInto(tree: FolderTree, folderId: string | null, picked: string | null): string | null {
  return folderId === null ? picked : (departmentOf(tree, folderId)?.id ?? null);
}

/** The next sort_order for a new department: one after the last. */
export function nextSortOrder(departments: readonly Pick<Folder, "sort_order">[]): number {
  return departments.reduce((most, department) => Math.max(most, (department.sort_order ?? -1) + 1), 0);
}

/**
 * The departments after moving one up (-1) or down (+1), each with its new sort_order
 * (0, 1, 2...); only the ones whose order changed are returned. [] at an end. O(D).
 */
export function reorder(
  departments: readonly Folder[],
  id: string,
  step: -1 | 1,
): { id: string; sort_order: number }[] {
  const at = departments.findIndex((department) => department.id === id);
  const to = at + step;
  if (at < 0 || to < 0 || to >= departments.length) return [];
  const order = [...departments];
  const [moved] = order.splice(at, 1);
  if (!moved) return [];
  order.splice(to, 0, moved);
  return order
    .map((department, index) => ({ id: department.id, sort_order: index, before: department.sort_order }))
    .filter((department) => department.before !== department.sort_order)
    .map(({ id: changed, sort_order }) => ({ id: changed, sort_order }));
}
