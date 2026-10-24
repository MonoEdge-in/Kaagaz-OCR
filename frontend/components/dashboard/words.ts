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
  toConfirmLink: "Confirm now",
  waitingBadge: "waiting",
  library: "Library",
  latest: "Latest",
  seeAll: "See all",
  allFolders: "All folders",
  fullRecord: "Full record",
  sortThem: "Sort them",
  askLabel: "Ask about your documents",
  askPlaceholder: "Ask anything, e.g. “Kiska bill sabse zyada tha last month?”",
  ask: "Ask",
  // Home made clear (24 Oct 2026): what the page is for, then one card per next step.
  lead: "Ask anything about your documents, or start with one of the steps below.",
  yourQuestions: "Your usual questions",
  questionsToTry: "Ready to ask",
  steps: "What you can do",
  checkLine: "The AI was not fully sure about these. A quick look makes every answer that uses them trusted.",
  checkClear: "Every value has been checked. New ones show here.",
  openReview: "Open Review",
  uploadDocuments: "Upload documents",
  uploadLine: "PDFs, photos and scans. Kaagaz reads each page and files it for you.",
  chooseFiles: "Choose files",
  browseLine: "Every document with its source page. Search or ask about any of them.",
  openDocuments: "Open documents",
};

/** The Review card's title: "Check 39 values"; null while the count loads. */
export function checkTitle(values: number | null): string {
  if (values === null) return "Check values";
  if (values === 0) return "Nothing to check";
  return `Check ${formatNumber(values)} ${values === 1 ? "value" : "values"}`;
}

/** The library card's title: "Browse 9 documents"; null while the count loads. */
export function browseTitle(documents: number | null): string {
  if (documents === null) return "Browse documents";
  return `Browse ${formatNumber(documents)} ${documents === 1 ? "document" : "documents"}`;
}

/** "Tuesday · 20 Oct 2026 · Wakad Realty", in India time. */
export function todayLine(now: Date, organisation: string): string {
  const day = new Intl.DateTimeFormat("en-IN", { weekday: "long", timeZone: "Asia/Kolkata" }).format(now);
  const date = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" }).format(now);
  return `${day} · ${date} · ${organisation}`;
}

/** "pdf" from "Rent Agreement.pdf"; "DOC" when the name has none. Upper case, at most 4 letters. */
export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  const ext = dot > 0 ? name.slice(dot + 1) : "";
  return /^[a-z0-9]{1,4}$/i.test(ext) ? ext.toUpperCase() : "DOC";
}

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
