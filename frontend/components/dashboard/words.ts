// Every word on the dashboard (File management D1), in one place so it is easy to change,
// and the small pure functions behind it (tests/file-management.test.ts).

import { formatNumber } from "@/lib/format";
import type { DocType } from "@/lib/types";

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
  reviewQueue: "Review queue",
  reviewLine: "values need a second look. Answers that use them say “Ready to verify” until they are checked.",
  reviewClear: "Nothing waits for a second look. New values to check show here.",
  openReview: "Open Review",
  libraryPanel: "Your library",
  libraryLine: "documents, each with its source page. Ask about any of them in English or Hinglish.",
  openDocuments: "Open documents",
  uploadDocuments: "Upload documents",
  uploadAutofile: "Kaagaz files them",
};

/** "4 versions and 23 values are waiting for you."; `values` is null for a member, who has no Review. */
export function waitingText(versions: number, values: number | null): string {
  const parts: string[] = [];
  if (versions > 0) parts.push(`${formatNumber(versions)} ${versions === 1 ? "version" : "versions"}`);
  if (values !== null && values > 0) parts.push(`${formatNumber(values)} ${values === 1 ? "value" : "values"}`);
  if (parts.length === 0) return "Everything is up to date.";
  const one = parts.length === 1 && (versions === 1 || values === 1);
  return `${parts.join(" and ")} ${one ? "is" : "are"} waiting for you.`;
}

/** "Tuesday · 20 Oct 2026 · Wakad Realty", in India time. */
export function todayLine(now: Date, organisation: string): string {
  const day = new Intl.DateTimeFormat("en-IN", { weekday: "long", timeZone: "Asia/Kolkata" }).format(now);
  const date = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" }).format(now);
  return `${day} · ${date} · ${organisation}`;
}

// Questions across all the documents, by the kinds the library holds most of (no AI makes them).
const SUGGESTIONS: Partial<Record<DocType, string>> = {
  invoice: "Which invoice had the largest total last month?",
  bank_statement: "What were the bank charges last month?",
  contract: "Which agreements end in the next three months?",
  land_record: "Who are the owners named in the land records?",
  receipt: "How much did we pay to each party this month?",
  register: "Which register entry has the largest amount?",
};
const ANY_SUGGESTION = "What did we upload this week?";

/** Up to three questions to ask with one press, for the most common kinds first. */
export function suggestedQuestions(types: readonly { doc_type: DocType | null }[]): string[] {
  const picked = types.map((row) => (row.doc_type ? SUGGESTIONS[row.doc_type] : undefined)).filter((q): q is string => !!q);
  return [...new Set([...picked, ANY_SUGGESTION])].slice(0, 3);
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
