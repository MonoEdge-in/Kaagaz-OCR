// The decisions behind the admin screen, kept out of the components so they
// are unit tested in node (tests/shell-admin.test.ts). The server checks every
// one of these again; these checks only give a quicker, clearer message.

import { sessionScope } from "@/components/shell/gate";
import { ApiError } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import type { StatusTone } from "@/lib/format";
import type { AdminUser, DocumentStatus, Me, PageRoute, Role, Stats } from "@/lib/types";

/**
 * "allow" only for an admin with an admin session (PRD A1); anyone else is
 * sent to /admin/login. The server refuses every /api/admin call to an app
 * session anyway (403 admin_session_required).
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
  { id: "failed", label: "Failed jobs" },
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

export interface Tile {
  label: string;
  value: string;
}

const STATUS_WORDS: Record<DocumentStatus, string> = {
  received: "Documents waiting for the virus check",
  clean: "Documents being read",
  ready: "Documents ready",
  needs_reader: "Documents stored, not readable yet",
  rejected: "Documents rejected",
  failed: "Documents that failed",
};

const STATUS_ORDER: readonly DocumentStatus[] = [
  "ready",
  "received",
  "clean",
  "needs_reader",
  "rejected",
  "failed",
];

/**
 * The Numbers tab, in a fixed order, with Indian digit grouping. Documents by
 * status count only current versions out of Trash; the versions kept behind
 * them and what is in Trash have tiles of their own.
 */
export function statTiles(stats: Stats): Tile[] {
  const documents = STATUS_ORDER.reduce(
    (sum, status) => sum + (stats.documents_by_status[status] ?? 0),
    0,
  );
  const tiles: Tile[] = [{ label: "Documents", value: formatNumber(documents) }];
  for (const status of STATUS_ORDER) {
    tiles.push({
      label: STATUS_WORDS[status],
      value: formatNumber(stats.documents_by_status[status] ?? 0),
    });
  }
  tiles.push(
    { label: "Other versions kept", value: formatNumber(stats.older_versions) },
    { label: "In Trash", value: formatNumber(stats.in_trash) },
    { label: "Questions asked", value: formatNumber(stats.questions_total) },
    { label: "Answered", value: formatNumber(stats.answered) },
    { label: "Not found in the documents", value: formatNumber(stats.not_found) },
    { label: "Answers labelled Verified", value: formatNumber(stats.verified_answers) },
    { label: "Answers labelled Needs review", value: formatNumber(stats.needs_review_answers) },
    { label: "Thumbs up", value: formatNumber(stats.feedback_up) },
    { label: "Thumbs down", value: formatNumber(stats.feedback_down) },
    { label: "Values waiting for review", value: formatNumber(stats.open_review_items) },
  );
  return tiles;
}

const ROUTE_ORDER: readonly PageRoute[] = ["cells", "text_layer", "ocr", "blank"];

const ROUTE_TILE_WORDS: Record<PageRoute, string> = {
  cells: "Spreadsheet cells",
  text_layer: "PDF text",
  ocr: "OCR",
  blank: "Blank pages",
};

/**
 * The Numbers tab's row "Pages read this month, by route" (PRD 2, R6): the
 * usage meter for pricing later. Always the same 4 tiles in the same order,
 * with 0 for a route that read nothing.
 */
export function pageRouteTiles(stats: Pick<Stats, "pages_this_month">): Tile[] {
  return ROUTE_ORDER.map((route) => ({
    label: ROUTE_TILE_WORDS[route],
    value: formatNumber(stats.pages_this_month[route] ?? 0),
  }));
}

/**
 * An audit row's details as one line of plain text: "count: 3, status: ready".
 * The server keeps only ids, statuses and counts there; nested values are shown
 * as compact JSON text, never as markup.
 */
export function describeDetails(details: Record<string, unknown>): string {
  return Object.keys(details)
    .sort()
    .map((key) => {
      const value = details[key];
      const text =
        typeof value === "string" || typeof value === "number" || typeof value === "boolean"
          ? String(value)
          : value === null
            ? "none"
            : JSON.stringify(value);
      return `${key}: ${text}`;
    })
    .join(", ");
}

const ACTION_WORDS: Record<string, string> = {
  "auth.login": "Signed in",
  "auth.logout": "Signed out",
  "document.upload": "Uploaded a document",
  "document.delete": "Deleted a document",
  "chat.ask": "Asked a question",
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
};

/** The audit action in words; an action this screen does not know is shown as is. */
export function describeAction(action: string): string {
  return ACTION_WORDS[action] ?? action;
}

export const AUDIT_PAGE_SIZE = 50;

export const FAILED_PAGE_SIZE = 50;

/** When a failed document was last tried (PRD 3 Z1). */
export function lastRunText(lastRunAt: string | null): string {
  return lastRunAt === null ? "Never run" : `Last tried ${formatDateTime(lastRunAt)}`;
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
