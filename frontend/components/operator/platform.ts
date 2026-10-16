// The operator's Activity and Health tabs in words, kept pure so vitest can
// prove them. The server sends codes and counts only (no details, target id,
// person or document); an unknown code is still said safely, as plain text.

import { describeAction } from "@/components/admin/logic";
import { formatNumber, statusChip } from "@/lib/format";
import type { DocumentStatus, PlatformEvent, PlatformHealth, ReaderReport, ReaderState } from "@/lib/types";

/** Platform actions (the operator's own log); the organisations' ones use the admin screen's words. */
const PLATFORM_ACTIONS: Record<string, string> = {
  "customer.create": "Created an organisation",
  "customer.create_refused": "Organisation not created: e-mail in use",
  "customer.disable": "Disabled an organisation",
  "customer.enable": "Enabled an organisation",
  "customer.delete_scheduled": "Scheduled an organisation's deletion",
  "customer.delete_refused": "Deletion refused: wrong name",
  "customer.delete_cancelled": "Cancelled an organisation's deletion",
  "org.purged": "Organisation deleted for good",
  "org.swept": "Deleted organisation checked again",
  "org.purge_now": "Organisation deleted at once (server)",
  "org.autofile_grant": "Allowed auto-filing",
  "org.autofile_revoke": "Stopped auto-filing",
  "admin.create": "Created an admin",
  "admin.create_refused": "Admin not created: e-mail in use",
  "admin.disable": "Disabled an admin",
  "admin.enable": "Enabled an admin",
  "admin.unlock": "Unlocked an admin",
  "admin.reset_totp": "Reset an admin's two-step",
  "operator.login": "Operator signed in",
  "operator.login_failed": "Operator sign-in failed",
  "operator.locked": "Operator sign-in locked",
  "operator.logout": "Operator signed out",
  "operator.step_up_failed": "Two-step code refused",
  "operator.step_up_locked": "Two-step code locked",
  "operator.create": "Operator made (server)",
  "operator.enrolled": "Operator two-step set up",
  "operator.unlock": "Operator unlocked (server)",
  "operator.reset_totp": "Operator two-step reset (server)",
};

/** "document.reprocess" -> "Document reprocess": any code, said as plain text. */
function plain(code: string): string {
  const words = code.replace(/[._]+/g, " ").trim();
  return words ? words[0]!.toUpperCase() + words.slice(1) : "Unknown";
}

export function actionWords(action: string): string {
  if (Object.hasOwn(PLATFORM_ACTIONS, action)) return PLATFORM_ACTIONS[action]!;
  const known: unknown = describeAction(action); // its map is a plain object: "constructor" is not a word
  return typeof known === "string" && known !== action ? known : plain(action);
}

/** A Load more and its failure belong to the filter they were asked under. */
export interface LoadMore {
  key: string;
  busy: boolean;
  failed: string | null;
}

export function moreFor(state: LoadMore, key: string): LoadMore {
  return state.key === key ? state : { key, busy: false, failed: null };
}

/** The action filter: actions starting with the value (the server's `action`). */
export const ACTION_FILTERS = [
  { value: "", label: "All actions" },
  { value: "customer.", label: "Organisations" },
  { value: "admin.", label: "Admins (by an operator)" },
  { value: "org.", label: "Deletion for good" },
  { value: "operator.", label: "Operator sign-in and codes" },
  { value: "user.", label: "Users (by their admins)" },
  { value: "auth.", label: "Sign-in in organisations" },
  { value: "document.", label: "Documents" },
] as const;

const BY: Record<PlatformEvent["by"], string> = { user: "A user", operator: "An operator", system: "The system" };

export function byWords(by: string): string {
  return Object.hasOwn(BY, by) ? BY[by as PlatformEvent["by"]] : plain(by);
}

export function targetWords(target: string | null): string {
  if (!target) return "None";
  return target === "customer" ? "Organisation" : plain(target);
}

// --- Health ---------------------------------------------------------------------

const FAILURES: Record<string, string> = {
  too_many_pages: "Too many pages",
  retries_exhausted: "Tried too many times",
  ocr_failed: "Scan reader failed on a page",
  customer_disabled: "Organisation was disabled",
  virus_found: "Virus found",
  blocked_content: "Blocked content",
  unscannable: "Could not be checked for viruses",
  needs_reader: "No reader for this kind of file",
  unknown: "No reason recorded",
};

export function failureWords(code: string): string {
  return Object.hasOwn(FAILURES, code) ? FAILURES[code]! : plain(code);
}

const STATUSES: readonly DocumentStatus[] = ["ready", "received", "clean", "needs_reader", "rejected", "failed"];

/** "12 Ready", "2 Failed": the document app's status words, known ones first, none at 0. */
export function countsInWords(counts: Record<string, number>, words: (code: string) => string): string[] {
  const known = STATUSES.filter((status) => status in counts) as string[];
  const order = [...known, ...Object.keys(counts).filter((code) => !known.includes(code)).sort()];
  return order.filter((code) => (counts[code] ?? 0) > 0).map((code) => `${formatNumber(counts[code] ?? 0)} ${words(code)}`);
}

export function statusWords(status: string): string {
  return (STATUSES as readonly string[]).includes(status) ? statusChip(status as DocumentStatus).text : plain(status);
}

/** How long the oldest document in progress has waited: "under a minute", "2 hours". */
export function waitWords(seconds: number | null): string {
  if (seconds === null) return "Nothing waiting";
  const units: [number, string, string][] = [
    [86400, "day", "days"],
    [3600, "hour", "hours"],
    [60, "minute", "minutes"],
  ];
  for (const [size, one, many] of units) {
    const count = Math.floor(seconds / size);
    if (count >= 1) return `${formatNumber(count)} ${count === 1 ? one : many}`;
  }
  return "under a minute";
}

// --- The reader and the queues (PRD 5 U9): status only ------------------------------

const READER_STATES: Record<ReaderState, string> = {
  working: "Working",
  failing: "Slow",
  down: "Down",
  key_problem: "Key problem",
};

/** A worker that has not reported for this long shows "Not reporting" (V4). */
export const NOT_REPORTING_MS = 2 * 60 * 1000;

/** A worker's reader status in words; "Not reporting" once its last report is 2 minutes old. */
export function readerWords(report: Pick<ReaderReport, "state" | "seen_at">, now: number): string {
  const seen = Date.parse(report.seen_at);
  if (!Number.isFinite(seen) || now - seen >= NOT_REPORTING_MS) return "Not reporting";
  return Object.hasOwn(READER_STATES, report.state) ? READER_STATES[report.state] : plain(report.state);
}

/** The jobs waiting in each lane; "Not answering" when the queue did not answer (null). */
export function queueWords(queues: PlatformHealth["queues"]): { label: string; value: string }[] {
  const lane = (count: number | undefined) => (queues ? formatNumber(count ?? 0) : "Not answering");
  return [
    { label: "Waiting in the express lane", value: lane(queues?.express) },
    { label: "Waiting in the main lane", value: lane(queues?.main) },
  ];
}
