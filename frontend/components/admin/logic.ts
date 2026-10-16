// The decisions behind the admin screen, kept out of the components so they
// are unit tested in node (tests/shell-admin.test.ts). The server checks every
// one of these again; these checks only give a quicker, clearer message.

import { sessionScope } from "@/components/shell/gate";
import { roleName } from "@/components/shell/nav";
import { ApiError } from "@/lib/api";
import { formatDateTime, formatNumber, formatUsd, PART_NOT_KNOWN, statusChip } from "@/lib/format";
import type { StatusTone } from "@/lib/format";
import type {
  AdminUser,
  AuditEntry,
  CustomerSettings,
  DocumentStatus,
  Me,
  PageRoute,
  Role,
  Stats,
  UsageBudget,
  UsageKind,
  UsageReport,
} from "@/lib/types";

/**
 * "allow" only for an admin with an admin session (PRD A1); anyone else is
 * sent to sign in (/login?next=/admin). The server refuses every /api/admin call
 * to an app session anyway (403 admin_session_required).
 */
export type AdminGate = "allow" | "admin_sign_in";

export function adminGate(me: Me): AdminGate {
  return me.user.role === "admin" && sessionScope(me) === "admin" ? "allow" : "admin_sign_in";
}

export const TABS = [
  { id: "users", label: "Users" },
  { id: "settings", label: "Settings" },
  { id: "activity", label: "Activity" },
  { id: "numbers", label: "Numbers" },
  { id: "usage", label: "Usage" },
  { id: "failed", label: "Failed documents" },
] as const;

export type TabId = (typeof TABS)[number]["id"];

/** Arrow keys, Home and End move between tabs (the WAI-ARIA tabs pattern). */
export function tabAfterKey(index: number, key: string, count: number): number | null {
  switch (key) {
    case "ArrowRight":
      return (index + 1) % count;
    case "ArrowLeft":
      return (index - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}

export const ROLES: readonly Role[] = ["member", "reviewer", "admin"];

export const MIN_PASSWORD_LENGTH = 12;

export interface NewUserInput {
  email: string;
  display_name: string;
  role: Role;
  password: string;
}

export type NewUserErrors = Partial<Record<"email" | "display_name" | "password", string>>;

/** Messages per field; an empty object means the form may be sent. */
export function validateNewUser(input: NewUserInput): NewUserErrors {
  const errors: NewUserErrors = {};
  const email = input.email.trim();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) errors.email = "Enter a valid e-mail address.";
  if (input.display_name.trim() === "") errors.display_name = "Enter a name.";
  if (input.password.length < MIN_PASSWORD_LENGTH) {
    errors.password = `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  return errors;
}

/** The new-user fields in screen order, and the id of each one's input. */
export const NEW_USER_FIELD_IDS = {
  email: "new-user-email",
  display_name: "new-user-name",
  password: "new-user-password",
} as const;

/** The first field (in screen order) with a problem, for focus; null if none. */
export function firstInvalidField(errors: NewUserErrors): keyof NewUserErrors | null {
  const order: (keyof NewUserErrors)[] = ["email", "display_name", "password"];
  return order.find((key) => errors[key] !== undefined) ?? null;
}

// --- The Users tab's status column and row actions --------------------------

// India time on a 24-hour clock ("14:05"), whatever the browser's own zone is,
// like every other time in the app (lib/format.ts).
const lockTimeFormat = new Intl.DateTimeFormat("en-IN", {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "Asia/Kolkata",
});

export function lockedUntilText(iso: string): string {
  return `Locked until ${lockTimeFormat.format(new Date(iso))}`;
}

/**
 * Locked while the lock's end is still ahead of `now` (milliseconds). The
 * server sends null once a lock has passed; the check here also covers a list
 * that was loaded before the lock ran out. An unreadable time is no lock.
 */
export function isLocked(user: Pick<AdminUser, "locked_until">, now: number): boolean {
  if (user.locked_until === null) return false;
  const end = Date.parse(user.locked_until);
  return Number.isFinite(end) && end > now;
}

export interface StatusPart {
  text: string; // always shown, so colour is never the only signal
  tone: StatusTone; // .status-chip data-tone
}

/**
 * The status column: Active, "Locked until 14:05" or Disabled, then "2FA on"
 * or "2FA off" (off is only worth saying for an admin, who can turn it on for
 * the admin screen), then "Temporary password" until the user sets their own.
 */
export function userStatus(user: AdminUser, now: number): StatusPart[] {
  const parts: StatusPart[] = [];
  if (user.disabled) parts.push({ text: "Disabled", tone: "danger" });
  else if (user.locked_until !== null && isLocked(user, now)) {
    parts.push({ text: lockedUntilText(user.locked_until), tone: "danger" });
  } else parts.push({ text: "Active", tone: "ready" });
  if (user.totp_enabled) parts.push({ text: "2FA on", tone: "neutral" });
  else if (user.role === "admin") parts.push({ text: "2FA off", tone: "neutral" });
  if (user.must_change_password) parts.push({ text: "Temporary password", tone: "neutral" });
  if (user.can_delete_forever) parts.push({ text: "Can delete forever", tone: "neutral" });
  return parts;
}

export interface UserActions {
  toggleDisabled: boolean;
  unlock: boolean;
  resetPassword: boolean;
  reset2fa: boolean;
}

/**
 * Which buttons a row shows. Nothing on the admin's own row: they cannot
 * disable themselves or reset their own 2FA (the server refuses both), and
 * they change their own password by signing in with it.
 */
export function userActions(
  user: AdminUser,
  { isSelf, now }: { isSelf: boolean; now: number },
): UserActions {
  if (isSelf) {
    return { toggleDisabled: false, unlock: false, resetPassword: false, reset2fa: false };
  }
  return {
    toggleDisabled: true,
    unlock: isLocked(user, now),
    resetPassword: true,
    reset2fa: user.totp_enabled,
  };
}

/** The row's Delete forever button: none for an admin or yourself; Stop when allowed; else
 * Allow, which needs your own two-step sign-in (the server says 403 two_step_required). */
export type ForeverControl =
  | { kind: "none" }
  | { kind: "stop" }
  | { kind: "allow"; disabledReason: string | null };

export function foreverControl(
  user: AdminUser,
  { isSelf, adminTwoStep }: { isSelf: boolean; adminTwoStep: boolean },
): ForeverControl {
  if (isSelf || user.role === "admin") return { kind: "none" };
  if (user.can_delete_forever) return { kind: "stop" };
  return {
    kind: "allow",
    disabledReason: adminTwoStep ? null : "Turn on your two-step sign-in in Settings to allow this.",
  };
}

/**
 * A temporary password for a new user (UI audit AD3): `length` characters from letters
 * and digits that cannot be mistaken for each other (no 0/O, 1/l/I), drawn with the
 * browser's crypto. Rejection sampling, so every character is equally likely.
 */
const PASSWORD_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789";

export function generatePassword(length = 16, random: (bytes: Uint8Array) => Uint8Array = (b) => crypto.getRandomValues(b)): string {
  const size = PASSWORD_ALPHABET.length;
  const limit = 256 - (256 % size); // bytes at or over this would favour the first characters
  let out = "";
  while (out.length < length) {
    for (const byte of random(new Uint8Array(length * 2))) {
      if (byte < limit && out.length < length) out += PASSWORD_ALPHABET[byte % size];
    }
  }
  return out;
}

/** AD7 (UI audit, 16 Oct 2026), with two-step required for admins (0035): what an admin without it sees. */
export function twoStepNote(): string {
  return "Admins without two-step sign-in set it up at their next sign-in.";
}

export function allowForeverQuestion(name: string): string {
  return (
    `Let ${name} delete documents in Trash forever? This covers every document in Trash, ` +
    "not only theirs, and can't be undone. They will type their password each time."
  );
}

export type UploadLimit = { ok: true; value: number | null } | { ok: false; message: string };

/**
 * The upload limit box: empty means "use the platform limit" (null), otherwise
 * a whole number of MB from 1 up to the platform limit.
 */
export function parseUploadLimit(text: string, platformMaxMb: number): UploadLimit {
  const trimmed = text.trim();
  if (trimmed === "") return { ok: true, value: null };
  if (!/^\d+$/.test(trimmed)) return { ok: false, message: "Enter a whole number of MB." };
  const value = Number(trimmed);
  if (value < 1) return { ok: false, message: "The limit must be at least 1 MB." };
  if (value > platformMaxMb) {
    return { ok: false, message: `The limit can be at most ${platformMaxMb} MB.` };
  }
  return { ok: true, value };
}

// Auto-filing (feature 28): the Settings switch's words. Plain words, no cost words.
export const AUTOFILE_LABEL = "Auto-filing";
export const AUTOFILE_HELP =
  "New files uploaded without a folder go into a folder for their type, like Invoices or Bank statements. " +
  "Files already uploaded stay where they are. Files you put in a folder yourself are never moved. " +
  "If a file lands in the wrong folder, press Undo or move it.";
export const AUTOFILE_NOT_AVAILABLE = "Auto-filing isn't available for your organisation yet.";

/** AD6: the form differs from what is saved (the Save bar shows only then). */
export function settingsChanged(
  saved: Pick<CustomerSettings, "max_upload_mb" | "autofile">,
  text: string,
  autofileOn: boolean,
): boolean {
  const savedText = saved.max_upload_mb === null ? "" : String(saved.max_upload_mb);
  return text.trim() !== savedText || (saved.autofile.granted && autofileOn !== saved.autofile.on);
}

/** What a Save that did not work says: the plain words for a switch not allowed yet, else the server's. */
export function settingsErrorText(caught: unknown): string {
  if (caught instanceof ApiError && caught.code === "not_granted") return AUTOFILE_NOT_AVAILABLE;
  return caught instanceof ApiError
    ? caught.message
    : "Could not save the settings. Check your connection, then try again.";
}

export interface Tile {
  label: string;
  value: string;
  note?: string;
  tip?: string; // a tooltip on the label, shown on hover and focus
}

/** The Ready to verify tile's tooltip. Which values are labelled so is many rules
 *  (backend/kaagaz/extraction/labels.py), not one number, so it names none. */
export const TO_VERIFY_TIP =
  "Values a second reading did not confirm, such as a word that was hard to read or two readings that differ. Open Review to verify them.";

const STATUS_ORDER: readonly DocumentStatus[] = [
  "ready",
  "received",
  "clean",
  "needs_reader",
  "rejected",
  "failed",
];

/**
 * AD4 (UI audit, 16 Oct 2026): the Numbers tab's four headline numbers, large, with Indian
 * digit grouping. Documents count only current versions out of Trash.
 */
export function kpiTiles(stats: Stats): Tile[] {
  const by = stats.documents_by_status;
  const documents = STATUS_ORDER.reduce((sum, status) => sum + (by[status] ?? 0), 0);
  return [
    { label: "Total documents", value: formatNumber(documents) },
    { label: "Ready", value: formatNumber(by.ready ?? 0) },
    {
      label: "Ready to verify",
      value: formatNumber(stats.open_review_items),
      note: "Values to verify in Review",
      tip: TO_VERIFY_TIP,
    },
    { label: "Failed", value: formatNumber(by.failed ?? 0) },
  ];
}

const ROUTE_ORDER: readonly PageRoute[] = ["cells", "text_layer", "ocr", "blank"];

const ROUTE_WORDS: Record<PageRoute, string> = {
  cells: "From spreadsheets",
  text_layer: "From PDF text",
  ocr: "Scanned pages",
  blank: "Blank pages",
};

/** A small list under the headline numbers; `none` is said when every row is 0. */
export interface Breakdown {
  title: string;
  rows: Tile[];
  none: string;
}

/**
 * AD4: everything else, in small lists with plain labels, in a fixed order. A row that is 0
 * is left out, so the tab is not a wall of zeros; a list with nothing in it says so.
 */
export function breakdowns(stats: Stats): Breakdown[] {
  const by = stats.documents_by_status;
  const kept = (rows: [string, number][]): Tile[] =>
    rows.filter(([, n]) => n > 0).map(([label, n]) => ({ label, value: formatNumber(n) }));
  return [
    {
      title: "Pipeline",
      none: "Nothing is waiting or being read.",
      rows: kept([
        ["Waiting for the virus check", by.received ?? 0],
        ["Being read", by.clean ?? 0],
        ["Stored, not readable yet", by.needs_reader ?? 0],
        ["Rejected", by.rejected ?? 0],
      ]),
    },
    {
      title: "Questions",
      none: "No questions asked yet.",
      rows: kept([
        ["Questions asked", stats.questions_total],
        ["Answered", stats.answered],
        ["Not found in the documents", stats.not_found],
        ["Answers labelled Verified", stats.verified_answers],
        ["Answers labelled Ready to verify", stats.needs_review_answers],
        ["Thumbs up", stats.feedback_up],
        ["Thumbs down", stats.feedback_down],
      ]),
    },
    {
      title: "Pages read this month",
      none: "No pages read this month.",
      rows: kept(ROUTE_ORDER.map((route) => [ROUTE_WORDS[route], stats.pages_this_month[route] ?? 0])),
    },
    {
      title: "Kept",
      none: "No older versions and nothing in Trash.",
      rows: kept([
        ["Other versions kept", stats.older_versions],
        ["In Trash", stats.in_trash],
      ]),
    },
  ];
}

// The audit details the Activity tab shows, in words; any other key (an id, the model a
// Read again asked for) is left out (UI quality council, 15 Oct 2026).
const plural = (n: number, word: string) => `${formatNumber(n)} ${word}${n === 1 ? "" : "s"}`;

function detailText(key: string, value: unknown): string | null {
  if (key === "page_no" && typeof value === "number") return `Page ${value}`;
  if (key === "versions" && typeof value === "number") return plural(value, "version");
  if (key === "tag_count" && typeof value === "number") return plural(value, "tag");
  if (key === "max_upload_mb") {
    return typeof value === "number" ? `Upload limit ${value} MB` : "Upload limit: the platform's";
  }
  if (key === "role" && ROLES.includes(value as Role)) return `Role: ${roleName(value as Role)}`;
  if (key === "status" && STATUS_ORDER.includes(value as DocumentStatus)) {
    return `Status: ${statusChip(value as DocumentStatus).text}`;
  }
  return null;
}

/** An audit row's details as one line of plain text: "Page 3, Status: Ready". */
export function describeDetails(details: Record<string, unknown>): string {
  return Object.keys(details)
    .sort()
    .map((key) => detailText(key, details[key]))
    .filter((text) => text !== null)
    .join(", ");
}

const TARGET_WORDS: Record<string, string> = {
  document: "Document",
  document_story: "Document",
  extracted_value: "Value",
  folder: "Folder",
  tag: "Tag",
  user: "User",
  user_word: "Word",
  message: "Chat",
  conversation: "Chat",
  customer: "Settings",
};

/** What an audit row was done on, as a word; never its id. */
export function describeTarget(targetType: string | null): string {
  return (targetType !== null && TARGET_WORDS[targetType]) || "—";
}

const ACTION_WORDS: Record<string, string> = {
  "auth.login": "Signed in",
  "auth.logout": "Signed out",
  "document.upload": "Uploaded a document",
  "document.version": "Uploaded a new version",
  "document.delete": "Deleted a document",
  "document.trash": "Moved a document to Trash",
  "document.restore": "Restored a document from Trash",
  "document.move": "Moved a document",
  "document.rename": "Renamed a document",
  "document.tags": "Changed a document's tags",
  "document.reprocess": "Asked to read a document again",
  "document.read_upgrade": "Read a document again after an upgrade",
  "document.page_accept": "Kept a page as it was read",
  "document_story.saved": "Saved a document's story",
  "document_story.deleted": "Deleted a document's story",
  "folder.create": "Added a folder",
  "folder.rename": "Renamed a folder",
  "folder.move": "Moved a folder",
  "folder.delete": "Deleted a folder",
  "tag.create": "Added a tag",
  "tag.delete": "Deleted a tag",
  "user_word.added": "Added a word to My words",
  "user_word.changed": "Changed a word in My words",
  "user_word.deleted": "Deleted a word from My words",
  "customer.create": "Organisation set up",
  "org.self_signup": "Created the organisation at sign-up",
  "customer.enable": "Organisation turned on",
  "customer.disable": "Organisation turned off",
  "chat.ask": "Asked a question",
  "chat.rename": "Renamed a conversation",
  "chat.delete": "Deleted a conversation",
  "review.resolve": "Corrected a value",
  "user.create": "Added a user",
  "user.update": "Changed a user",
  "settings.update": "Changed settings",
  "auth.admin_login": "Signed in to admin",
  "auth.login_failed": "Sign-in failed",
  "auth.locked": "Account locked after wrong passwords",
  "auth.enrol": "Set up 2FA",
  "auth.totp_on": "Turned on 2FA",
  "auth.totp_off": "Turned off 2FA",
  "auth.password_change": "Changed their password",
  "auth.logout_everywhere": "Signed out everywhere",
  "user.unlock": "Unlocked a user",
  "user.reset_password": "Reset a user's password",
  "user.reset_totp": "Reset a user's 2FA",
  "user.delete_forever_on": "Allowed a user to delete forever",
  "user.delete_forever_off": "Stopped a user deleting forever",
  // Feature 28: auto-filing.
  "document.autofile": "Kaagaz filed a document",
  "document.autofile_undo": "Undid automatic filing",
  "settings.autofile_on": "Turned auto-filing on",
  "settings.autofile_off": "Turned auto-filing off",
  "org.autofile_grant": "Auto-filing allowed",
  "org.autofile_revoke": "Auto-filing stopped",
};

/** The audit action in words; an action this screen does not know is shown as is. */
export function describeAction(action: string, details: Record<string, unknown> = {}): string {
  if (action === "folder.create" && details.auto === true) return "Kaagaz made a folder";
  return ACTION_WORDS[action] ?? action;
}

// What Kaagaz did by itself (feature 28) has no person: the Who column says "Kaagaz".
const BY_KAAGAZ = new Set(["document.autofile", "org.autofile_grant", "org.autofile_revoke"]);

/** Who did it: the person's name, "Kaagaz" for auto-filing, else a dash (a removed user). */
export function whoText(entry: Pick<AuditEntry, "action" | "actor_name" | "details">): string {
  if (entry.actor_name !== null) return entry.actor_name;
  const kaagaz = BY_KAAGAZ.has(entry.action) || (entry.action === "folder.create" && entry.details.auto === true);
  return kaagaz ? "Kaagaz" : "—";
}

export const AUDIT_PAGE_SIZE = 50;

export const FAILED_PAGE_SIZE = 50;

/** When a failed document was last tried (PRD 3 Z1). */
export function lastRunText(lastRunAt: string | null): string {
  return lastRunAt === null ? "Not tried yet" : `Last tried ${formatDateTime(lastRunAt)}`;
}

/**
 * What a Retry that did not start says: the server's own words for a refusal
 * (409, someone already retried it; 429, too many at once), else a network hint.
 */
export function retryErrorText(caught: unknown): string {
  return caught instanceof ApiError
    ? caught.message
    : "Could not reach the server. Check your connection, then try again.";
}

// --- The Usage tab (model choice SP2, SP4) ---------------------------------------

export type UsagePeriod = "today" | "week" | "month" | "custom";

export const USAGE_PERIODS: readonly { id: UsagePeriod; label: string }[] = [
  { id: "today", label: "Today" },
  { id: "week", label: "Last 7 days" },
  { id: "month", label: "This month" },
  { id: "custom", label: "Custom" },
];

// The server counts days in India time (TIMEZONE), so the browser does too.
const dayFormat = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" });

/** Today in India time as YYYY-MM-DD; `now` in milliseconds. */
export function indiaDay(now: number): string {
  return dayFormat.format(new Date(now));
}

/**
 * The days a period covers, both included (YYYY-MM-DD). Custom takes the two
 * date boxes, and is null until both are filled with the start first.
 */
export function usageRange(
  period: UsagePeriod,
  custom: { from: string; to: string },
  now: number,
): { from: string; to: string } | null {
  const today = indiaDay(now);
  switch (period) {
    case "today":
      return { from: today, to: today };
    case "week":
      return { from: new Date(Date.parse(today) - 6 * 86_400_000).toISOString().slice(0, 10), to: today };
    case "month":
      return { from: `${today.slice(0, 8)}01`, to: today };
    case "custom":
      return custom.from !== "" && custom.to !== "" && custom.from <= custom.to ? custom : null;
  }
}

/** What the Usage tab shows: keyless and empty are different (one can never fill). */
export type UsageState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "off" }
  | { kind: "empty" }
  | { kind: "ready"; report: UsageReport };

export function usageState(report: UsageReport | null, error: string | null): UsageState {
  if (error !== null) return { kind: "error", message: error };
  if (report === null) return { kind: "loading" };
  if (report.totals.calls > 0) return { kind: "ready", report };
  return report.ai_enabled === false ? { kind: "off" } : { kind: "empty" };
}

/** "$12.40 of $50.00 this month", or that there is no budget. */
export function budgetLine(
  budget: Pick<UsageBudget, "ai_budget_usd" | "spent_this_month_usd"> & { spent_part_unknown?: boolean },
): string {
  if (budget.ai_budget_usd === null) return "No monthly AI budget is set.";
  const spent = formatUsd(budget.spent_this_month_usd) + (budget.spent_part_unknown ? ` ${PART_NOT_KNOWN}` : "");
  return `${spent} of ${formatUsd(budget.ai_budget_usd)} this month`;
}

const KIND_WORDS: Record<UsageKind, string> = {
  scan: "Reading scanned pages",
  upgrade: "Reading again after an upgrade",
  card: "Document cards",
  invoice: "Invoice tables",
  map: "Document maps",
  embed: "Search indexing",
  chat: "Chat answers",
  memory: "Chat memory",
  search: "Search",
  story: "Stories",
  careful: "Careful re-reads", // Dynamic (feature 26)
  ensemble_scan: "Second opinions", // Ensemble (feature 27): the other readers
  ensemble_chat: "Second opinions", // and the other answer drafts
};

/** The kind of work in words; a kind this screen does not know yet is shown as is. */
export function usageKindText(kind: UsageKind): string {
  return KIND_WORDS[kind] ?? kind;
}
