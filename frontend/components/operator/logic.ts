// The operator (super admin) screens' rules, kept pure so vitest can prove
// them: the sign-in form, an organisation's status and counts in words, the
// search, the new-organisation form, and what to say when the server says no.

import { codeError } from "@/app/login/twoStep";
import { NETWORK_FAILURE, SIGN_IN_DOWN, TOO_MANY_ATTEMPTS, emailError, passwordError } from "@/app/login/signIn";
import type { CloseWay } from "@/components/documents/ActionDialog";
import { ApiError } from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import { formatDate, formatNumber, formatRelative } from "@/lib/format";
import type { StatusTone } from "@/lib/format";
import type { OrgAdmin, OrgStatus, Organisation, SignInSummary } from "@/lib/types";

export const KEY_HELP = "Your two-step key is given to you on the server when your account is made.";
export const DISABLE_EFFECT = "Its users are signed out now and its jobs stop.";

// --- Sign-in -----------------------------------------------------------------

// The server sends one 401 for every failure (wrong details, no code, a used
// code, locked), so the screen says one thing too.
export const OPERATOR_SIGN_IN_FAILED =
  "Those details are not right. Check your e-mail, password and the newest code from your app. " +
  "After many wrong tries, sign-in stops for a while.";

export type OperatorField = "email" | "password" | "code";
export type OperatorErrors = Record<OperatorField, string | null>;

/** All three are needed: two-step sign-in is always on for operators. */
export function validateOperatorSignIn(email: string, password: string, code: string): OperatorErrors {
  return { email: emailError(email), password: passwordError(password), code: codeError(code) };
}

export function firstInvalidOperator(errors: OperatorErrors): OperatorField | null {
  if (errors.email) return "email";
  if (errors.password) return "password";
  if (errors.code) return "code";
  return null;
}

export function operatorSignInError(caught: unknown): string {
  if (caught instanceof ApiError) {
    if (caught.status === 400 || caught.status === 401) return OPERATOR_SIGN_IN_FAILED;
    if (caught.status === 429) return TOO_MANY_ATTEMPTS;
    if (caught.status === 0) return NETWORK_FAILURE;
    return SIGN_IN_DOWN;
  }
  return caught instanceof TypeError ? NETWORK_FAILURE : SIGN_IN_DOWN;
}

// --- The session ---------------------------------------------------------------

export const OPERATOR_SIGN_IN = "/operator/login";
/** Its own, not the customer app's: an operator's sign-out never reaches a customer tab. */
export const OPERATOR_CHANNEL = "kaagaz-operator-session";
export const SIGNED_OUT = "signed_out";
export const SIGN_OUT_FAILED = "Could not sign out. Check your connection and try again.";
/** After the session's end, ask the server again this often while it still says yes (clocks differ). */
export const RECHECK_MS = 30_000;

export type ExitReason = "ended" | "signed_out";

/** Where to go once the session is gone. "Your session ended" only when this tab had one. */
export function exitUrl(reason: ExitReason, hadSession: boolean): string {
  if (reason === "signed_out") return `${OPERATOR_SIGN_IN}?signed_out=1`;
  return hadSession ? `${OPERATOR_SIGN_IN}?ended=1` : OPERATOR_SIGN_IN;
}

/** GET /me failed: no session (401) leaves for sign-in; anything else shows Try again. */
export function meFailed(caught: unknown): "signed_out" | "error" {
  return caught instanceof ApiError && caught.status === 401 ? "signed_out" : "error";
}

/** Milliseconds until the session ends (expires_at in Unix seconds), within one browser timer. */
export function expiryDelay(expiresAt: number, now: number): number {
  return Math.min(2 ** 31 - 1, Math.max(0, expiresAt * 1000 - now));
}

/** Sign out on the server (it ends every session of this operator); a 401 means
 * it already had. The page to leave for, or what to say. */
export async function signOut(logout: () => Promise<unknown>): Promise<{ leave: string } | { error: string }> {
  try {
    await logout();
  } catch (caught) {
    if (!(caught instanceof ApiError && caught.status === 401)) return { error: SIGN_OUT_FAILED };
  }
  return { leave: exitUrl("signed_out", true) };
}

// --- Dialogs ---------------------------------------------------------------------

/** Never while busy: the reply would be lost. Otherwise every way closes (no password is shown any more). */
// eslint-disable-next-line @typescript-eslint/no-unused-vars -- the way no longer matters
export function canCloseDialog(state: { busy: boolean }, _way: CloseWay): boolean {
  return !state.busy;
}

/** A change's reply clears the open dialog only if it is still that change's. */
export function afterDone<T>(open: T | null, finished: T): T | null {
  return open === finished ? null : open;
}

/** After a failed /me at the session's end: a 401 has left already; anything else asks again later. */
export function recheckAfter(caught: unknown): number | null {
  return meFailed(caught) === "signed_out" ? null : RECHECK_MS;
}

// --- Any other refusal -------------------------------------------------------

export const STEP_UP_FAILED =
  "That code was not accepted. Wait for the next code in your app and try again. " +
  "After many wrong codes, this stops for a while.";
export const ORG_NOT_FOUND = "We couldn't find that organisation or admin. Reload the list.";
export const CHECK_INPUT = "Check what you typed and try again.";
const SERVER_FALLBACK = "Something went wrong."; // lib/api.ts, when the reply has no message
export const OPERATOR_DOWN = "Something went wrong on our side. Try again in a minute.";
export const NAME_MISMATCH = "That is not the organisation's name. Type it exactly, with the same capitals and spaces.";
export const CONFLICT = "That change is not possible now. Reload the list and try again.";
/** The most output tokens a page the server accepts (#463), said on the field and for its refusal. */
export const TOKENS_RANGE = "Choose a number from 1,024 to 65,536.";
export const DAILY_PAGES_RANGE = "Choose a daily page limit from 1 to 1,000,000 pages, or leave it empty."; // #478
// #488: the server's words for ceiling_out_of_range, said by the field's own check too.
export const CEILING_RANGE = "Choose from ₹0.20 to ₹0.55 a page, or leave it empty.";

/** The server's error codes in plain words (the server sends most refusals as
 * 409 "conflict" with its own sentence, which is used then). */
export const REFUSALS: Record<string, string> = {
  step_up_failed: STEP_UP_FAILED,
  step_up_locked: "Too many wrong codes. Wait a few minutes, then try again with a new code.",
  locked: "Too many wrong codes. Wait a few minutes, then try again with a new code.",
  name_mismatch: NAME_MISMATCH,
  not_disabled: "Disable the organisation before deleting it.",
  deleting: "This organisation is being deleted.",
  taken: "That e-mail is already in use.",
  not_found: ORG_NOT_FOUND,
  // Model choice (council 11 Oct 2026) and AI settings (features 25-27, C10): a model, an
  // organisation's settings and the platform defaults.
  not_eligible: "This model does not pass the catalogue's rules now, so it can't be enabled.",
  not_verified: "A model has not passed verify-models for the current prompt. Run it with --record first.",
  chat_not_verified: "An answering model has not passed verify-models for the current answer prompt. Run it with --record first.",
  in_use: "An organisation or the platform defaults still use this model. Remove it from every organisation and the defaults first.",
  unavailable: "One of these models is not enabled on the platform now, or can't do this work. Reload and choose again.",
  default_not_allowed: "Choose each default from its own ticked list.",
  level_not_supported: "A chosen model does not accept one of these thinking levels. Choose levels every model accepts.",
  limit_out_of_range: TOKENS_RANGE, // the most output tokens a page (#463)
  pages_out_of_range: DAILY_PAGES_RANGE, // the daily page limit (#478)
  mistral_not_set_up: "Mistral isn't set up on this server yet.", // #473
  // #488: ultra low cost, in the server's words.
  ceiling_out_of_range: CEILING_RANGE,
  ulc_needs_gemini: "Ultra low cost needs a Gemini model with a known price that can read with minimal thinking.",
};

/** Plain words for a refused change. A 409 keeps the server's own sentence
 * ("This organisation is being deleted.", "That e-mail is already in use."). */
export function operatorErrorMessage(caught: unknown): string {
  if (!(caught instanceof ApiError)) return caught instanceof TypeError ? NETWORK_FAILURE : OPERATOR_DOWN;
  const known = Object.hasOwn(REFUSALS, caught.code) ? REFUSALS[caught.code] : undefined;
  if (known) return known;
  if (caught.status === 403) return errorWords(caught, "operator"); // #468: never "on our side"
  if (caught.status === 409) return caught.message && caught.message !== SERVER_FALLBACK ? caught.message : CONFLICT;
  if (caught.status === 404) return ORG_NOT_FOUND;
  if (caught.status === 400) return CHECK_INPUT;
  if (caught.status === 401) return "Your session ended. Sign in again.";
  if (caught.status === 429) return TOO_MANY_ATTEMPTS;
  if (caught.status === 0) return caught.code === "timeout" ? caught.message : NETWORK_FAILURE;
  return OPERATOR_DOWN;
}

// --- One organisation in words --------------------------------------------------

export interface OrgStatusText {
  text: string;
  tone: StatusTone;
  /** "Deleted on 13 Oct 2026": only while it is being deleted. */
  note: string | null;
}

export function orgStatus(
  org: Pick<Organisation, "status" | "delete_after"> & Partial<Pick<Organisation, "admin_email_confirmed">>,
): OrgStatusText {
  if (org.status === "active") return { text: "Active", tone: "ready", note: null };
  if (org.status === "pending") {
    // #457: Approve needs the admin's email confirmed first.
    const note = org.admin_email_confirmed ? "Email confirmed" : "Email not confirmed yet";
    return { text: "Waiting for approval", tone: "working", note };
  }
  if (org.status === "deleting") {
    const date = org.delete_after ? formatDate(org.delete_after) : "";
    return { text: "Being deleted", tone: "danger", note: date ? `Deleted on ${date}` : null };
  }
  return { text: "Disabled", tone: "neutral", note: null };
}

/** "1 admin", "12 members": Indian digit grouping, one or many. */
export function countText(count: number, one: string, many: string): string {
  return `${formatNumber(count)} ${count === 1 ? one : many}`;
}

export function userCounts(users: Organisation["users"]): string[] {
  return [
    countText(users.admins, "admin", "admins"),
    countText(users.reviewers, "reviewer", "reviewers"),
    countText(users.members, "member", "members"),
  ];
}

// --- Deleting an organisation ----------------------------------------------------

/** The server's grace period (PR 4, PROVISIONAL); the server sets the real date. */
export const GRACE_DAYS = 7;
const DAY_MS = 24 * 60 * 60 * 1000;

/** The words of the delete dialog, with the date it would be deleted on. */
export function deleteEffect(now: number): string {
  const date = formatDate(new Date(now + GRACE_DAYS * DAY_MS).toISOString());
  return `Everything is deleted on ${date}. You can cancel until then. Backups keep data for 7 more days.`;
}

/** The countdown on a row being deleted: "6 days left", "Less than a day left". */
export function deleteCountdown(deleteAfter: string | null, now: number): string | null {
  if (!deleteAfter) return null;
  const left = Date.parse(deleteAfter) - now;
  if (Number.isNaN(left)) return null;
  if (left <= 0) return "Being removed now";
  if (left < DAY_MS) return "Less than a day left";
  return countText(Math.floor(left / DAY_MS), "day left", "days left");
}

/** The name must be typed exactly: same capitals and spaces (the server compares it as typed). */
export function nameError(typed: string, name: string): string | null {
  if (!typed) return "Type the organisation's name.";
  return typed === name ? null : NAME_MISMATCH;
}

export function lastActivityText(iso: string | null, now: number): string {
  return iso ? formatRelative(iso, now) || "No activity yet" : "No activity yet";
}

// --- Search -------------------------------------------------------------------

export function filterOrgs<T extends { name: string }>(items: readonly T[], search: string): T[] {
  const wanted = search.trim().toLocaleLowerCase();
  return wanted ? items.filter((org) => org.name.toLocaleLowerCase().includes(wanted)) : [...items];
}

/** The "Waiting for approval" filter (#457): only the pending sign-ups, or every one. */
export function waitingOnly<T extends Pick<Organisation, "status">>(items: readonly T[], waiting: boolean): T[] {
  return waiting ? items.filter((org) => org.status === "pending") : [...items];
}

/** The one status line under the search box. */
export function searchSummary(shown: number, total: number, search: string): string {
  const all = countText(total, "organisation", "organisations");
  return search.trim() ? `${formatNumber(shown)} of ${all} match.` : `${all}.`;
}

// --- A new organisation, or one more admin of one -------------------------------

export interface NewOrgInput {
  name: string;
  admin_email: string;
  admin_name: string;
  /** One more admin: a fresh code (step-up). */
  code?: string;
}

export type NewOrgErrors = Partial<Record<keyof NewOrgInput, string>>;

export const NEW_ORG_FIELDS = ["name", "admin_email", "admin_name", "code"] as const;

/** As the server: names trimmed, 1 to 200 characters (organisation) or 1 to 100
 * (admin). One more admin (adminOnly) needs no organisation name, but a code. */
export function validateNewOrg(input: NewOrgInput, adminOnly = false): NewOrgErrors {
  const errors: NewOrgErrors = {};
  const who = adminOnly ? "the admin's" : "the first admin's";
  const name = input.name.trim();
  const adminName = input.admin_name.trim();
  if (adminOnly) {
    const code = codeError(input.code ?? "");
    if (code) errors.code = code;
  } else if (!name) errors.name = "Enter the organisation's name.";
  else if (name.length > 200) errors.name = "Use at most 200 characters.";
  const email = emailError(input.admin_email);
  if (email) errors.admin_email = input.admin_email.trim() ? email : `Enter ${who} e-mail address.`;
  if (!adminName) errors.admin_name = `Enter ${who} name.`;
  else if (adminName.length > 100) errors.admin_name = "Use at most 100 characters.";
  return errors;
}

/** A 409 for an e-mail in use: said under the e-mail field. */
export function isTakenEmail(caught: unknown): boolean {
  return caught instanceof ApiError && (caught.code === "taken" || caught.message === REFUSALS.taken);
}

export function firstInvalidOrgField(errors: NewOrgErrors): keyof NewOrgInput | null {
  return NEW_ORG_FIELDS.find((field) => errors[field]) ?? null;
}

// --- One organisation's admins ---------------------------------------------------

/** The short facts beside an admin's name: locked, disabled. Keycloak always asks for the code. */
export function adminFacts(admin: Pick<OrgAdmin, "locked" | "disabled">): string[] {
  return [
    ...(admin.locked ? ["Locked"] : []),
    ...(admin.disabled ? ["Disabled"] : []),
  ];
}

export function lastSignInText(iso: string | null, now: number): string {
  const when = iso ? formatRelative(iso, now) : "";
  return when ? `Last sign-in ${when}` : "Never signed in";
}

/** Which changes fit an admin as they are now (none while the organisation is being deleted). */
export function adminActions(admin: Pick<OrgAdmin, "locked" | "disabled">, orgStatus: OrgStatus) {
  if (orgStatus === "deleting") return [];
  return [
    admin.disabled ? ("enable" as const) : ("disable" as const),
    ...(admin.locked ? (["unlock"] as const) : []),
    "resend-setup" as const,
    "reset-2fa" as const,
  ];
}

// --- Company sign-in (#737) ------------------------------------------------------

/** "12 people are on company sign-in. 5 still sign in the old way." */
export function moveSummaryText(summary: Pick<SignInSummary, "connected" | "to_move">): string {
  const people = (n: number) => `${formatNumber(n)} ${n === 1 ? "person is" : "people are"}`;
  const still = summary.to_move === 1 ? "1 person still signs in the old way." : `${formatNumber(summary.to_move)} still sign in the old way.`;
  return `${people(summary.connected)} on company sign-in. ${still}`;
}

/** The named confirm button: how many people it moves. */
export function moveButtonText(count: number): string {
  return `Move ${formatNumber(count)} ${count === 1 ? "person" : "people"}`;
}

/** The server moves at most this many people per press. */
export const MOVE_AT_A_TIME = 50;

/** What moving does to the people moved, under the Move button's code field. */
export const MOVE_EFFECT =
  "Everyone moved is signed out now and can't use their old password. They sign in again after setting up from the email.";
