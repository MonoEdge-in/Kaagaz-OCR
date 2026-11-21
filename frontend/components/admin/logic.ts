// The decisions behind the admin screen, kept out of the components so they
// are unit tested in node (tests/shell-admin.test.ts). The server checks every
// one of these again; these checks only give a quicker, clearer message.

import { sessionScope } from "@/components/shell/gate";
import { roleName } from "@/components/shell/nav";
import { DOMAIN_UNCHECKED, providersText } from "@/components/account/signInWays";
import type { MineDraft, OrgDraft } from "@/lib/aiSettings";
import { ApiError } from "@/lib/api";
import type { Draft } from "@/lib/drafts";
import { errorWords } from "@/lib/errorWords";
import { formatDateTime, formatNumber, formatUsd, PART_NOT_KNOWN, statusChip } from "@/lib/format";
import type { StatusTone } from "@/lib/format";
import type {
  AddedUser,
  AdminUser,
  AiSettings,
  AiTask,
  AuditEntry,
  CustomerSettings,
  DocumentStatus,
  GoogleRules,
  Me,
  MemberNumbers,
  MemberPeriod,
  PageRoute,
  PendingConnection,
  Role,
  Stats,
  UsageBudget,
  UsageKind,
  UsageReport,
} from "@/lib/types";

/**
 * "allow" only for an admin with an admin session (PRD A1); an admin with an app
 * session is sent to sign in (/login?next=/admin); anyone else is told it is for
 * admins (N4, QA 17 Oct: signing in again would not help). The server refuses
 * every /api/admin call to an app session anyway (403 admin_session_required).
 */
export type AdminGate = "allow" | "admin_sign_in" | "no_access";

export function adminGate(me: Me): AdminGate {
  if (me.user.role !== "admin") return "no_access";
  return sessionScope(me) === "admin" ? "allow" : "admin_sign_in";
}

export const TABS = [
  { id: "users", label: "Users" },
  { id: "settings", label: "Settings" },
  { id: "departments", label: "Departments" }, // File management F1
  { id: "ai", label: "AI settings" },
  { id: "activity", label: "Activity" },
  { id: "numbers", label: "Numbers" },
  { id: "quality", label: "Answer quality" }, // PRD 6 F2
  { id: "usage", label: "Usage" },
  { id: "failed", label: "Failed documents" },
] as const;

export type TabId = (typeof TABS)[number]["id"];

/** Where an admin with an app session signs in, coming back to this admin screen (a person's
 * Access page too, #604); anything but a plain /admin path comes back to /admin. */
export function adminSignInPath(pathname: string): string {
  return /^\/admin(\/[\w-]+)*$/.test(pathname) ? `/login?next=${pathname}` : "/login?next=/admin";
}

/** The tab /admin?tab=… opens (PRD 7 lead decision 12): a tab's id, else Users. */
export function tabFrom(asked: string | undefined): TabId {
  return TABS.find((tab) => tab.id === asked)?.id ?? "users";
}

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
}

export type NewUserErrors = Partial<Record<"email" | "display_name", string>>;

/** Messages per field; an empty object means the form may be sent. */
export function validateNewUser(input: NewUserInput): NewUserErrors {
  const errors: NewUserErrors = {};
  const email = input.email.trim();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) errors.email = "Enter a valid e-mail address.";
  if (input.display_name.trim() === "") errors.display_name = "Enter a name.";
  return errors;
}

/** The new-user fields in screen order, and the id of each one's input. */
export const NEW_USER_FIELD_IDS = {
  email: "new-user-email",
  display_name: "new-user-name",
} as const;

/** The first field (in screen order) with a problem, for focus; null if none. */
export function firstInvalidField(errors: NewUserErrors): keyof NewUserErrors | null {
  const order: (keyof NewUserErrors)[] = ["email", "display_name"];
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
 * The status column: Active, "Locked until 14:05" or Disabled, then "Two-step: On" or
 * "Two-step: Off" for every user (#457 phase 3: anyone may have codes), then "Temporary
 * password" until the user sets their own.
 */
export function userStatus(user: AdminUser, now: number): StatusPart[] {
  const parts: StatusPart[] = [];
  if (user.disabled) parts.push({ text: "Disabled", tone: "danger" });
  else if (user.locked_until !== null && isLocked(user, now)) {
    parts.push({ text: lockedUntilText(user.locked_until), tone: "danger" });
  } else parts.push({ text: "Active", tone: "ready" });
  if (user.auth_authority === "keycloak") {
    parts.push({ text: "Company-managed sign-in", tone: "neutral" });
  } else {
    // Plain words (25 Oct 2026): "Two-step", as the rest of the product says, not "2FA".
    parts.push({ text: user.totp_enabled ? "Two-step: On" : "Two-step: Off", tone: "neutral" });
  }
  if (user.must_change_password) parts.push({ text: "Temporary password", tone: "neutral" });
  if (user.can_delete_forever) parts.push({ text: "Can delete for good", tone: "neutral" });
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
    reset2fa: user.role !== "admin", // an admin's authenticator is the super admin's to reset
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
    disabledReason: adminTwoStep ? null : "Turn on two-step sign-in in Account to allow this.",
  };
}

/** AD7 (UI audit, 16 Oct 2026), with two-step required for admins (0035): what an admin without it sees. */
/** The toast after Add user (#737): the server's own result line, a problem when the setup email
 * could not be sent (the person is added; Resend setup email finishes it). */
export function addedNotice(user: Pick<AddedUser, "setup_email_sent" | "message">): {
  text: string;
  problem?: boolean;
} {
  return user.setup_email_sent ? { text: user.message } : { problem: true, text: user.message };
}

export function twoStepNote(): string {
  return "Admins without two-step sign-in set it up at their next sign-in.";
}

export function allowForeverQuestion(name: string): string {
  return (
    `Let ${name} delete documents in Trash for good? This covers every document in Trash, ` +
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

// Connected accounts (#457 phase 1): the People and Settings words, naming only the providers
// that are on (Me's providers, #457 review).
export const AUTO_APPROVE_LABEL = "Approve new sign-in connections automatically";
export const AUTO_APPROVE_SAVED = "Saved.";
export const AUTO_APPROVE_NOTE = "This setting saves as soon as you change it.";
export const CONNECTIONS_FAILED = "Couldn't load connections to approve. Try again.";
export const ALREADY_DECIDED = "Another admin already decided this one.";

export function autoApproveWarning(providers: readonly string[]): string {
  return `Members' ${providersText(providers)} accounts will work at once, without your review.`;
}

export function removeConnectionsQuestion(name: string, providers: readonly string[]): string {
  return `Remove ${name}'s connected accounts? They can't sign in with ${providersText(providers)} any more. Their password still works.`;
}

const WAY_NAMES: Record<string, string> = { password: "Password", google: "Google" };

/** Approve's and Decline's names for a screen reader: "Approve Ramesh Kumar's Google account". */
export function decideLabel(row: PendingConnection, approve: boolean): string {
  return `${approve ? "Approve" : "Decline"} ${row.display_name}'s ${WAY_NAMES[row.provider] ?? row.provider} account`;
}

/** "Password, Google": how this person signs in (every account keeps its password). */
export function signsInWith(ways: readonly string[] | undefined): string {
  return (ways ?? ["password"]).map((way) => WAY_NAMES[way] ?? way).join(", ");
}

/** #507: the tag beside how they sign in, while their role signs in with Google only and they
 * have no Google account that passes (AdminUser.google_needed). */
export const GOOGLE_NEEDED = "Needs to connect Google";

/** The tag's words: their connected account's domain is not checked yet (#507 review,
 * AdminUser.google_unchecked: the limited session's words), else "Needs to connect Google";
 * null: no tag. */
export function googleTag(user: AdminUser): string | null {
  if (user.google_unchecked === true) return DOMAIN_UNCHECKED;
  return user.google_needed === true ? GOOGLE_NEEDED : null;
}
export const RESET_DISCONNECTS = "This also disconnects their Google account.";

/** #507, Add a user: the hint under the role while Google sign-in is on for it (as the invite
 * email's line: the server's Google sign-in, the super admin's, and the role allowed). */
export const ADD_USER_GOOGLE_HINT =
  "Your organisation signs in with Google for this role. After they choose a password, they connect their Google account.";

/** The hint for this role; null while Google is off (``google`` left out: the server's switch). */
export function addUserGoogleHint(google: GoogleRules | undefined, role: Role): string | null {
  return google?.available === true && google.allowed_roles.includes(role) ? ADD_USER_GOOGLE_HINT : null;
}

/** Asked before Reset password; it also removes their connected accounts (#507). */
export function resetPasswordQuestion(name: string, ways: readonly string[] | undefined): string {
  const question = `Reset ${name}'s password? They are signed out everywhere and must choose a new one.`;
  return (ways ?? []).some((way) => way !== "password") ? `${question} ${RESET_DISCONNECTS}` : question;
}

const askedDay = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });

/** A waiting connection in one line: "Ramesh Kumar · Google · ramesh.k@gmail.com · asked 30 Oct". */
export function pendingLine(row: PendingConnection): string {
  const parts = [row.display_name, WAY_NAMES[row.provider] ?? row.provider, row.email ?? "no email given"];
  return `${parts.join(" · ")} · asked ${askedDay.format(new Date(row.linked_at))}`;
}

/** AD6: the form differs from what is saved (the Save bar shows only then). */
export function settingsChanged(
  saved: Pick<CustomerSettings, "max_upload_mb" | "autofile">,
  text: string,
  autofileOn: boolean,
): boolean {
  const savedText = saved.max_upload_mb === null ? "" : String(saved.max_upload_mb);
  return text.trim() !== savedText || (saved.autofile.granted && autofileOn !== saved.autofile.on);
}

// --- Drafts (#468): unsent admin work kept in this tab for the same admin (lib/drafts.ts) ---

export const ADD_USER_DRAFT = "admin:add-user";
export const SETTINGS_DRAFT = "admin:settings";
/** The admin's AI settings tab and a member's own (/settings/ai) keep separate drafts. */
export const aiSettingsDraft = (scope: "organisation" | "member") =>
  scope === "organisation" ? "admin:ai-settings" : "settings:ai";

/** Add a user's draft: email, name and role; null until something is typed. */
export function newUserDraft(input: NewUserInput): Draft | null {
  if (input.email === "" && input.display_name === "") return null;
  return { email: input.email, display_name: input.display_name, role: input.role };
}

/** The form from a kept draft; a value of the wrong kind is the empty form's. */
export function newUserFromDraft(draft: Draft | null): NewUserInput {
  const role = ROLES.find((each) => each === draft?.role) ?? "member";
  const text = (value: unknown) => (typeof value === "string" ? value : "");
  return { email: text(draft?.email), display_name: text(draft?.display_name), role };
}

/** Settings' draft: only the fields that differ from what is saved, so restoring it never undoes
 * another admin's change to the other one (#468 review, CR1); null when nothing does. */
export function settingsDraft(saved: CustomerSettings, text: string, autofileOn: boolean): Draft | null {
  const draft: Draft = {};
  if (text.trim() !== (saved.max_upload_mb === null ? "" : String(saved.max_upload_mb))) draft.uploadLimit = text;
  if (saved.autofile.granted && autofileOn !== saved.autofile.on) draft.autofile = autofileOn;
  return Object.keys(draft).length > 0 ? draft : null;
}

/** The Settings form from what is saved, with a kept draft over it (auto-filing only while allowed). */
export function settingsFromDraft(draft: Draft | null, saved: CustomerSettings): { text: string; autofileOn: boolean } {
  const savedText = saved.max_upload_mb === null ? "" : String(saved.max_upload_mb);
  return {
    text: typeof draft?.uploadLimit === "string" ? draft.uploadLimit : savedText,
    autofileOn: saved.autofile.granted && typeof draft?.autofile === "boolean" ? draft.autofile : saved.autofile.on,
  };
}

/** AI settings' changed tasks as plain values to keep; of an organisation's task, only the part
 * changed from `saved` (its level, its lock), as Settings does (CR1); null when none changed. */
export function aiChangesToKeep(saved: OrgDraft | MineDraft, changes: OrgDraft | MineDraft): Draft | null {
  const kept: Draft = {};
  for (const [task, value] of Object.entries(changes)) {
    if (value === undefined) continue;
    if (value === null || typeof value === "string") {
      kept[task] = value;
      continue;
    }
    const before = (saved as OrgDraft)[task as AiTask];
    const part: Draft = {};
    if (value.level !== before?.level) part.level = value.level;
    if (value.locked !== before?.locked) part.locked = value.locked;
    kept[task] = part;
  }
  return Object.keys(kept).length > 0 ? kept : null;
}

/** An organisation's kept changes over what is saved: tasks the reply has, with a level allowed
 * now; only the parts kept, the rest as saved now. */
export function orgDraftOver(saved: OrgDraft, kept: Draft | null, settings: AiSettings): OrgDraft {
  const draft = { ...saved };
  for (const task of settings.tasks) {
    const value = kept?.[task.task];
    const now = saved[task.task];
    if (typeof value !== "object" || value === null || Array.isArray(value) || !now) continue;
    const level = "level" in value ? task.allowed.find((each) => each === value.level) : now.level;
    if (level) draft[task.task] = { level, locked: typeof value.locked === "boolean" ? value.locked : now.locked };
  }
  return draft;
}

/** A member's kept changes over what is saved: tasks not locked, an allowed level or null (the organisation's). */
export function mineDraftOver(saved: MineDraft, kept: Draft | null, settings: AiSettings): MineDraft {
  const draft = { ...saved };
  for (const task of settings.tasks) {
    if (task.locked || !kept || !(task.task in kept)) continue;
    const value = kept[task.task];
    if (value === null) draft[task.task] = null;
    else {
      const level = task.allowed.find((each) => each === value);
      if (level) draft[task.task] = level;
    }
  }
  return draft;
}

export const LEAVE_UNSAVED = "You have unsaved changes. Leave without saving?";

/** Another admin tab may open: at once with nothing unsaved, else only once the admin says leave. */
export function tabChangeAllowed(unsaved: boolean, confirmLeave: () => boolean): boolean {
  return !unsaved || confirmLeave();
}

/** What a Save that did not work says: the plain words for a switch not allowed yet, else the server's. */
export function settingsErrorText(caught: unknown): string {
  if (caught instanceof ApiError && caught.code === "not_granted") return AUTOFILE_NOT_AVAILABLE;
  return errorWords(caught);
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
  "locked", // PRD 7 (#605)
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
      title: "Not ready yet", // plain words (25 Oct 2026): it was "Pipeline"
      none: "Nothing is waiting or being read.",
      rows: kept([
        ["Waiting for the virus check", by.received ?? 0],
        ["Being read", by.clean ?? 0],
        ["Stored, not readable yet", by.needs_reader ?? 0],
        ["Rejected", by.rejected ?? 0],
        ["Locked", by.locked ?? 0], // PRD 7 (#605): never read
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
        ["Useful", stats.feedback_up],
        ["Not useful", stats.feedback_down],
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

// --- #386: each member's numbers, and the sort shared with the operator's table (#387) ---

export const MEMBER_PERIODS: readonly { id: MemberPeriod; label: string }[] = [
  { id: "month", label: "This month" },
  { id: "30d", label: "Last 30 days" },
  { id: "all", label: "All time" },
];

export type SortDir = "ascending" | "descending";
export interface SortState<K extends string> {
  column: K;
  dir: SortDir;
}

/**
 * The sort after a press on `column`'s header: the same column turns round; another starts
 * with the most first, or A to Z for a name.
 */
export function nextSort<K extends string>(current: SortState<K>, column: K, nameColumn: K): SortState<K> {
  if (current.column === column) return { column, dir: current.dir === "ascending" ? "descending" : "ascending" };
  return { column, dir: column === nameColumn ? "ascending" : "descending" };
}

/** `rows` sorted by `value`; a null (never active) always goes last, and ties go by name. */
export function sortedBy<T>(
  rows: readonly T[],
  value: (row: T) => number | string | null,
  dir: SortDir,
  name: (row: T) => string,
): T[] {
  const sign = dir === "ascending" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const [x, y] = [value(a), value(b)];
    if (x !== y) {
      if (x === null) return 1;
      if (y === null) return -1;
      const order = typeof x === "string" && typeof y === "string" ? x.localeCompare(y) : Number(x) - Number(y);
      if (order !== 0) return sign * order;
    }
    return name(a).localeCompare(name(b));
  });
}

export type MemberColumn = "name" | "documents_uploaded" | "pages_read" | "questions_asked" | "values_verified" | "last_active_at";

export const MEMBER_COLUMNS: readonly { id: MemberColumn; label: string }[] = [
  { id: "name", label: "Member" },
  { id: "documents_uploaded", label: "Files uploaded" },
  { id: "pages_read", label: "Pages read" },
  { id: "questions_asked", label: "Questions asked" },
  { id: "values_verified", label: "Values verified" },
  { id: "last_active_at", label: "Last active" },
];

export function sortMembers(members: readonly MemberNumbers[], sort: SortState<MemberColumn>): MemberNumbers[] {
  const value = (member: MemberNumbers) => (sort.column === "name" ? member.display_name : member[sort.column]);
  return sortedBy(members, value, sort.dir, (member) => member.display_name);
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

// #468: an empty Activity list says whether filters hid everything or nothing has happened yet
// (the admin's and the operator's tabs), and a date range may not end before it starts.
export const NO_ACTIVITY = "No activity yet.";
export const NO_ACTIVITY_MATCHES = "No activity matches these filters.";
export const activityEmptyText = (filtered: boolean) => (filtered ? NO_ACTIVITY_MATCHES : NO_ACTIVITY);

export const DATES_BACKWARDS = "From must be on or before To.";

/** DATES_BACKWARDS for From after To (YYYY-MM-DD, compared as text); null otherwise or while one is empty. */
export function datesError(from: string, to: string): string | null {
  return from !== "" && to !== "" && from > to ? DATES_BACKWARDS : null;
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
  feedback: "Answer",
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
  "document.replace_from_trash": "Replaced a document in Trash with a new upload", // #697
  "document.move": "Moved a document",
  "document.rename": "Renamed a document",
  "document.tags": "Changed a document's tags",
  "document.reprocess": "Asked to read a document again",
  "document.stop": "Stopped reading a document", // #713
  "document.read_upgrade": "Read a document again after an upgrade",
  "document.land_facts": "Read a land record's owners and places again", // PRD 6 D3
  "document.page_accept": "Kept a page as it was read",
  "document.page_turns": "Changed the turn of a document's pages", // #600: for everyone
  "document.line_corrected": "Corrected a line in a clean copy", // #527
  "document.line_correction_undone": "Undid a line correction in a clean copy", // #527
  "document_story.saved": "Saved a document's story",
  "document_story.deleted": "Deleted a document's story",
  "folder.create": "Added a folder",
  "folder.rename": "Renamed a folder",
  "folder.move": "Moved a folder",
  "folder.delete": "Deleted a folder",
  "folder.trash": "Moved a folder to Trash", // #542: ids and counts only, never a name
  "folder.restore": "Restored a folder from Trash",
  "folder.delete_forever": "Deleted a folder for good",
  "folder.upload": "Uploaded a folder", // #484: counts and ids only, never a name
  "folder.upload_check": "Folder upload checked for files already in Kaagaz", // #484: counts only
  "tag.create": "Added a tag",
  "tag.delete": "Deleted a tag",
  "user_word.added": "Added a word to My words",
  "user_word.changed": "Changed a word in My words",
  "user_word.deleted": "Deleted a word from My words",
  "customer.create": "Organisation set up",
  "org.self_signup": "Created the organisation at sign-up",
  "org.approved": "Organisation approved", // #457: a sign-up approved by the Kaagaz team
  "customer.enable": "Organisation turned on",
  "customer.disable": "Organisation turned off",
  "customer.clear_legacy_credentials": "Old passwords and two-step codes cleared",
  "chat.ask": "Asked a question",
  "chat.rename": "Renamed a conversation",
  "chat.delete": "Deleted a conversation",
  // PRD 6 F2: Answer quality.
  "feedback.handled": "Marked a Not useful answer as handled",
  "feedback.reopened": "Opened a Not useful answer again",
  "learning.remove": "Removed what Kaagaz learned about a document", // PRD 6 F3
  "review.resolve": "Corrected a value",
  "user.create": "Added a user",
  "user.update": "Changed a user",
  "settings.update": "Changed settings",
  "auth.admin_login": "Signed in to admin",
  "auth.login_failed": "Sign-in failed",
  "auth.locked": "Account locked after wrong passwords",
  "auth.enrol": "Set up two-step sign-in",
  "auth.totp_on": "Turned on two-step sign-in",
  "auth.totp_off": "Turned off two-step sign-in",
  "auth.password_change": "Changed their password",
  // #457 phase 0: email.
  "auth.email_confirmed": "Confirmed their email",
  "auth.password_reset": "Reset their password from an email",
  "auth.invite_sent": "Sent an invite",
  "auth.setup_email_sent": "Sent a setup email",
  "auth.invite_accepted": "Accepted an invite",
  "auth.logout_everywhere": "Signed out everywhere",
  // #457 phase 1: connected accounts.
  "auth.identity_linked": "Connected a Google account",
  "auth.identity_approved": "Approved a Google account",
  "auth.identity_declined": "Declined a Google account",
  "auth.identity_unlinked": "Disconnected a Google account",
  "auth.keycloak_linked": "Connected company sign-in",
  // #507: Google sign-in by organisation and role.
  "auth.identity_link_refused": "A Google account couldn't be connected",
  "auth.identity_reapproval": "A Google account waits for approval again",
  "settings.google_rules": "Changed the Google sign-in rules",
  "settings.google_rules_refused": "Google sign-in rules not saved: they would have locked you out",
  "org.google_signin_set": "Google sign-in changed for the organisation",
  "settings.auto_approve_on": "Turned on automatic approval of sign-in connections",
  "settings.auto_approve_off": "Turned off automatic approval of sign-in connections",
  // #603: Kaagaz on WhatsApp.
  "settings.whatsapp_on": "Turned WhatsApp on",
  "settings.whatsapp_off": "Turned WhatsApp off",
  "settings.whatsapp": "Changed the WhatsApp settings",
  "whatsapp.connected": "Connected a WhatsApp number",
  "whatsapp.disconnected": "A WhatsApp number was disconnected",
  "whatsapp.google_proof": "Confirmed with Google to connect WhatsApp",
  "whatsapp.file_link": "Got a link to a file on WhatsApp", // W4: the file itself is never sent
  "user.unlock": "Unlocked a user",
  "user.reset_password": "Reset a user's password",
  "user.reset_totp": "Reset a user's two-step sign-in",
  "auth.totp_reset": "Reset a user's two-step sign-in", // #457: the name since 0063; older rows keep user.reset_totp
  "user.delete_forever_on": "Allowed a user to delete for good",
  "user.delete_forever_off": "Stopped a user deleting for good",
  // Feature 28: auto-filing.
  "document.autofile": "Kaagaz filed a document",
  "document.autofile_undo": "Undid automatic filing",
  "settings.autofile_on": "Turned auto-filing on",
  "settings.autofile_off": "Turned auto-filing off",
  "org.autofile_grant": "Auto-filing allowed",
  "org.autofile_revoke": "Auto-filing stopped",
  // File management (PRD 1, Phase 2): owner, label, departments, versions and the log itself.
  "document.owner_change": "Changed a document's owner",
  "document.label_change": "Changed a document's label",
  "document.view": "Viewed a document",
  "document.download": "Downloaded a document",
  "folder.kind": "Changed a department",
  "version.suggested": "Kaagaz suggested a new version",
  "version.accepted": "Linked a new version",
  "version.rejected": "Kept a document separate",
  "version.undone": "Undid a version link",
  "version.make_current": "Made a version current",
  "document.version_separated": "Made a version a separate file",
  // #648: Admins' and Management's decisions on possible versions; the admin log only.
  "version.kept_apart": "Marked as not versions",
  "version.kept_apart_undone": "Undid not versions",
  "version.later": "Left for later",
  "version.linked": "Linked files as versions", // #648 (V-A): the admin log only
  "version.unlinked": "Undid linking versions",
  "audit.export": "Exported the activity",
  // #482 and #481 (3 Nov 2026): reads logged since, and the super admin's two-step rule.
  "search.run": "Searched inside documents",
  "usage.export": "Exported the AI usage",
  "org.admin_two_step_on": "Two-step sign-in required for admins",
  "org.admin_two_step_off": "Two-step sign-in no longer required for admins",
  "document.reading_released": "Reading started again after a pause", // QA P2: a held file let go
  // PRD 7 section 6 (PR 13): locked files, and every open, wrong password and block.
  "lock.lock": "Locked a file",
  "lock.unlock": "Unlocked a file",
  "lock.grant": "Allowed someone to open a locked file",
  "lock.revoke": "Stopped someone opening a locked file",
  "lock.open": "Opened locked files with their password",
  "lock.open_failed": "Typed a wrong password to open locked files",
  "lock.open_blocked": "Blocked from opening locked files after wrong passwords",
  "lock.view": "Viewed a locked file",
};

/** The audit action in words; an action this screen does not know is shown as is. */
export function describeAction(action: string, details: Record<string, unknown> = {}): string {
  if (action === "folder.create" && details.auto === true) return "Kaagaz made a folder";
  // #600: a save that left no page turned.
  if (action === "document.page_turns" && details.turned === 0) return "Put a document's pages back as scanned";
  if (action === "lock.open" && details.via === "google") return "Opened locked files with Google";
  if (action === "lock.open_failed" && details.via === "google") return "A Google sign-in to open locked files was refused";
  return ACTION_WORDS[action] ?? action;
}

// What Kaagaz did by itself (feature 28) has no person: the Who column says "Kaagaz".
const BY_KAAGAZ = new Set([
  "document.autofile",
  "org.autofile_grant",
  "org.autofile_revoke",
  "org.admin_two_step_on", // #481: the super admin's, as auto-filing's grant
  "org.admin_two_step_off",
  "org.google_signin_set", // #507: the super admin's switch
  "document.reading_released", // QA P2: the release sweep's, after a reading pause
]);
// Issue 375: with no person at all (actor_id null), these come from Kaagaz's own set-up
// commands (backend kaagaz/cli.py create-user and reset-admin-2fa), never from a screen.
const SET_UP_BY_KAAGAZ = new Set(["user.create", "user.reset_totp", "auth.totp_reset"]);

/** Who did it: the person's name, "Kaagaz" for auto-filing and set-up, else a dash (a removed user). */
export function whoText(
  entry: Pick<AuditEntry, "action" | "actor_name" | "details"> & Partial<Pick<AuditEntry, "actor_id">>,
): string {
  if (entry.actor_name !== null) return entry.actor_name;
  const kaagaz =
    BY_KAAGAZ.has(entry.action) ||
    (entry.action === "folder.create" && entry.details.auto === true) ||
    (entry.actor_id === null && SET_UP_BY_KAAGAZ.has(entry.action));
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
  return errorWords(caught);
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
  | { kind: "off"; report: UsageReport }
  | { kind: "empty"; report: UsageReport }
  | { kind: "ready"; report: UsageReport };

export function usageState(report: UsageReport | null, error: string | null): UsageState {
  if (error !== null) return { kind: "error", message: error };
  if (report === null) return { kind: "loading" };
  if (report.totals.calls > 0) return { kind: "ready", report };
  return report.ai_enabled === false ? { kind: "off", report } : { kind: "empty", report };
}

/** #478 (Tushar, 3 Nov 2026): "Pages read today: 1,240 of 2,000." while a daily page limit is
 * in effect (the India day), and that reading is paused once it is reached; null without one.
 * Never money. */
export function pagesTodayLine(report: Pick<UsageReport, "reading_today">): string | null {
  const today = report.reading_today;
  if (!today || today.limit === null) return null;
  const line = `Pages read today: ${formatNumber(today.pages)} of ${formatNumber(today.limit)}.`;
  return today.pages >= today.limit ? `${line} Reading is paused until the limit resets, or until it is raised.` : line; // QA C4
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
  land: "Land records", // PRD 6 D: owners, shares and places
  transcribe: "WhatsApp voice notes", // #603 W6
  ulc_scan: "Reading scanned pages", // #488: the org admin is never told it was Ultra low cost
};

/** The kind of work in words; a kind this screen does not know yet is shown as is. */
export function usageKindText(kind: UsageKind): string {
  return KIND_WORDS[kind] ?? kind;
}
