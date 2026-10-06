// The operator (super admin) screens' rules, kept pure so vitest can prove
// them: the sign-in form, an organisation's status and counts in words, the
// search, the new-organisation form, and what to say when the server says no.

import { codeError } from "@/app/admin/login/adminSignIn";
import { NETWORK_FAILURE, SIGN_IN_DOWN, TOO_MANY_ATTEMPTS, emailError, passwordError } from "@/app/login/signIn";
import { ApiError } from "@/lib/api";
import { formatDate, formatNumber, formatRelative } from "@/lib/format";
import type { StatusTone } from "@/lib/format";
import type { Organisation } from "@/lib/types";

export const KEY_HELP = "Your two-step key is given to you on the server when your account is made.";
export const DISABLE_EFFECT = "Its users are signed out now and its jobs stop.";
export const TEMPORARY_PASSWORD_NOTE =
  "Give this to the admin privately. They must change it at first sign-in.";

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

// --- Any other refusal -------------------------------------------------------

export const STEP_UP_FAILED =
  "That code was not accepted. Wait for the next code in your app and try again. " +
  "After many wrong codes, this stops for a while.";
export const ORG_NOT_FOUND = "We couldn't find this organisation. Reload the list.";
export const CHECK_INPUT = "Check what you typed and try again.";
export const OPERATOR_DOWN = "Something went wrong on our side. Try again in a minute.";

/** Plain words for a refused change. A 409 keeps the server's own sentence
 * ("This organisation is being deleted.", "That e-mail is already in use."). */
export function operatorErrorMessage(caught: unknown): string {
  if (!(caught instanceof ApiError)) return caught instanceof TypeError ? NETWORK_FAILURE : OPERATOR_DOWN;
  if (caught.code === "step_up_failed") return STEP_UP_FAILED;
  if (caught.status === 409) return caught.message;
  if (caught.status === 404) return ORG_NOT_FOUND;
  if (caught.status === 400) return CHECK_INPUT;
  if (caught.status === 401) return "Your session ended. Sign in again.";
  if (caught.status === 429) return TOO_MANY_ATTEMPTS;
  if (caught.status === 0) return NETWORK_FAILURE;
  return OPERATOR_DOWN;
}

// --- One organisation in words --------------------------------------------------

export interface OrgStatusText {
  text: string;
  tone: StatusTone;
  /** "Deleted on 13 Oct 2026": only while it is being deleted. */
  note: string | null;
}

export function orgStatus(org: Pick<Organisation, "status" | "delete_after">): OrgStatusText {
  if (org.status === "active") return { text: "Active", tone: "ready", note: null };
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

export function lastActivityText(iso: string | null, now: number): string {
  return iso ? formatRelative(iso, now) || "No activity yet" : "No activity yet";
}

// --- Search -------------------------------------------------------------------

export function filterOrgs<T extends { name: string }>(items: readonly T[], search: string): T[] {
  const wanted = search.trim().toLocaleLowerCase();
  return wanted ? items.filter((org) => org.name.toLocaleLowerCase().includes(wanted)) : [...items];
}

/** The one status line under the search box. */
export function searchSummary(shown: number, total: number, search: string): string {
  const all = countText(total, "organisation", "organisations");
  return search.trim() ? `${formatNumber(shown)} of ${all} match.` : `${all}.`;
}

// --- A new organisation ---------------------------------------------------------

export interface NewOrgInput {
  name: string;
  admin_email: string;
  admin_name: string;
}

export type NewOrgErrors = Partial<Record<keyof NewOrgInput, string>>;

export const NEW_ORG_FIELDS = ["name", "admin_email", "admin_name"] as const;

/** As the server: names trimmed, 1 to 200 characters (organisation) or 1 to 100 (admin). */
export function validateNewOrg(input: NewOrgInput): NewOrgErrors {
  const errors: NewOrgErrors = {};
  const name = input.name.trim();
  const adminName = input.admin_name.trim();
  if (!name) errors.name = "Enter the organisation's name.";
  else if (name.length > 200) errors.name = "Use at most 200 characters.";
  const email = emailError(input.admin_email);
  if (email) errors.admin_email = input.admin_email.trim() ? email : "Enter the first admin's e-mail address.";
  if (!adminName) errors.admin_name = "Enter the first admin's name.";
  else if (adminName.length > 100) errors.admin_name = "Use at most 100 characters.";
  return errors;
}

export function firstInvalidOrgField(errors: NewOrgErrors): keyof NewOrgInput | null {
  return NEW_ORG_FIELDS.find((field) => errors[field]) ?? null;
}
