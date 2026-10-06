// The operator (super admin) screens' rules, kept pure so vitest can prove
// them: the sign-in form, an organisation's status and counts in words, the
// search, and what to say when the server says no.

import { codeError } from "@/app/admin/login/adminSignIn";
import { NETWORK_FAILURE, SIGN_IN_DOWN, TOO_MANY_ATTEMPTS, emailError, passwordError } from "@/app/login/signIn";
import { ApiError } from "@/lib/api";
import { formatDate, formatNumber, formatRelative } from "@/lib/format";
import type { StatusTone } from "@/lib/format";
import type { Organisation } from "@/lib/types";

export const KEY_HELP = "Your two-step key is given to you on the server when your account is made.";

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

// --- Loading the list ---------------------------------------------------------

export const OPERATOR_DOWN = "Something went wrong on our side. Try again in a minute.";

/** Plain words for a failed request. A 401 also sends the browser to sign in. */
export function operatorErrorMessage(caught: unknown): string {
  if (!(caught instanceof ApiError)) return caught instanceof TypeError ? NETWORK_FAILURE : OPERATOR_DOWN;
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
