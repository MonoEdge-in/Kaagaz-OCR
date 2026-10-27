// Every word of the folder screen (File management F1, F2, F5), the document page's Details
// tab (P1, O1 to O3) and the admin Departments tab, in one place so the wording is easy to
// change. Pure functions here are unit tested in node (tests/file-management.test.ts).

import type { SortKey } from "@/components/documents/libraryQuery";
import { formatNumber } from "@/lib/format";

export const FILES_TEXT = {
  // F2: the upload's department. "Upload to" (issue 367): it picks where new files go, never filters the list.
  departmentLabel: "Upload to",
  noDepartment: "No department",
  // F5: the tree.
  departments: "Departments",
  other: "Other",
  notInDepartment: "Not in a department",
  // F5: the folder's own subfolders, and how the files show.
  subfolders: "Folders here",
  showAs: "Show as",
  list: "List",
  grid: "Grid",
  // F5, Q4: the list's columns.
  columns: {
    name: "Name",
    type: "Type",
    owner: "Owner",
    label: "Label",
    version: "Version",
    documentDate: "Document date",
    updatedAt: "Last updated",
    updatedBy: "Updated by",
  },
  sortBy: (column: string) => `Sort by ${column}`,
  // The Sort pill above the list (desktop/Documents.html): the same keys as the headers, so the grid sorts too.
  sortLabel: "Sort by",
  sortNames: {
    created_at: "Date uploaded",
    document_date: "Document date",
    updated_at: "Last updated",
    name: "Name",
    doc_type: "Type",
    owner: "Owner",
    sensitivity: "Label",
    version: "Version",
  } satisfies Record<SortKey, string>,
  dated: (date: string) => `Dated ${date}`,
  noOwner: "No owner",
  noDate: "No date",
  // P1: the Details tab.
  details: "Details",
  owner: "Owner",
  label: "Label",
  departmentPath: "Department",
  type: "Type",
  documentDate: "Document date",
  author: "Author",
  lastUpdated: "Last updated",
  pages: "Pages",
  notFiled: "Not in a department",
  change: "Change",
  saving: "Saving…",
  saved: (what: string) => `${what} saved.`,
  // F1: the admin Departments tab.
  adminTab: "Departments",
  adminIntro: "Departments are the top folders everyone files into. Their order is the order on Home and in the folder list.",
  newDepartment: "New department",
  addDepartment: "Add department",
  rename: "Rename",
  save: "Save",
  cancel: "Cancel",
  moveUp: "Move up",
  moveDown: "Move down",
  noDepartments: "No departments yet. Add the first one, such as Land & Legal or Accounts.",
  nameLabel: "Department name",
};

/** "v2"; "v1 · newer?" while a newer version waits to be confirmed (F5). */
export function versionCell(versionNo: number, newerWaiting: boolean): string {
  return newerWaiting ? `v${versionNo} · newer?` : `v${versionNo}`;
}

/** What a newer version waiting says, read out in full. */
export const NEWER_SPOKEN = "a newer version waits to be confirmed";

/** "12 files" on a subfolder tile; "…" until counted. */
export function tileCount(count: number | undefined): string {
  if (count === undefined) return "…";
  return `${formatNumber(count)} ${count === 1 ? "file" : "files"}`;
}
