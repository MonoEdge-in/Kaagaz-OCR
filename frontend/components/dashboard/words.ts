// Every word on the dashboard (File management D1), in one place so it is easy to change,
// and the small pure functions behind it (tests/file-management.test.ts).

import { formatNumber } from "@/lib/format";

export const DASHBOARD_TEXT = {
  title: "Home",
  upload: "Upload",
  tiles: {
    documents: "Documents",
    departments: "Departments",
    newVersions: "New versions this month",
    toConfirm: "Versions to confirm",
  },
  byType: "Documents by type",
  byTypeEmpty: "Types show here once documents are read.",
  departments: "Departments",
  departmentsEmpty: "No departments yet. An admin adds them in Admin, Departments.",
  notInDepartment: "Not in a department",
  notInDepartmentHint: "Open the list to move them into a department.",
  recent: "Recent documents",
  recentEmpty: "Nothing uploaded yet.",
  activity: "Recent activity",
  loading: "Loading your home screen…",
  loadFailed: "The home screen did not load.",
  tryAgain: "Try again",
  toConfirmLink: "Review them",
};

/** "Good morning, Priya": by the hour of the day; the first name only. */
export function greeting(name: string, hour: number): string {
  const first = name.trim().split(/\s+/)[0] || name;
  const part = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  return `${part}, ${first}`;
}

/** "+12 this week"; null for none. */
export function thisWeekText(count: number): string | null {
  return count > 0 ? `+${formatNumber(count)} this week` : null;
}

/** "12 files", "1 file". */
export function filesText(count: number): string {
  return `${formatNumber(count)} ${count === 1 ? "file" : "files"}`;
}

/**
 * Each bar's share of the longest one, in percent (one scale for all, D1), at least 2 so a
 * small count still shows. [] for no rows. O(n).
 */
export function barShares(counts: readonly number[]): number[] {
  const most = Math.max(0, ...counts);
  if (most === 0) return counts.map(() => 0);
  return counts.map((count) => (count === 0 ? 0 : Math.max(2, Math.round((count / most) * 100))));
}
