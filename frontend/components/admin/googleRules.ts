// Settings, Ways to sign in (#507), kept pure so vitest can prove it: per role "Google allowed"
// and "Google only" (only ever inside allowed), the company domains, the checks before saving
// (the server makes the same ones) and what a refused save says. The form keeps the domains as
// typed; they are read on Save.

import { ApiError } from "@/lib/api";
import type { GoogleRules, GoogleRulesChange, Role } from "@/lib/types";
import { settingsErrorText } from "./logic";

export const ROLE_CHOICES: readonly { role: Role; label: string }[] = [
  { role: "admin", label: "Admins" },
  { role: "reviewer", label: "Reviewers" },
  { role: "member", label: "Members" },
];

export const GOOGLE_ONLY_HINT =
  "Once their Google account is connected, their password stops working for sign-in. Until then it only lets them connect Google.";
export const DOMAINS_LABEL = "Company domains (optional)";
export const DOMAINS_HINT =
  "Only Google accounts from these domains can be used. Leave empty to allow any. Separate more than one with commas.";
export const GOOGLE_OFF_TEXT =
  "Google sign-in is off for your organisation. Everyone signs in with their email and password.";

export const ONLY_NEEDS_ALLOWED = "A role that uses Google only must also be allowed to use Google.";
export const DOMAIN_SHAPE = "Enter a domain like sharmatraders.com.";
export const PERSONAL_DOMAIN = "Gmail and other personal email domains can't be used. Enter your company's own domain.";
export const TOO_MANY_DOMAINS = "Add at most 10 domains.";
export const MAX_DOMAINS = 10;

export const LOCKOUT_NO_LINK =
  "Connect your own Google account first, in Account, Ways to sign in. Then you can make Google the only way in for admins.";
export const LOCKOUT_OUTSIDE =
  "Your own Google account isn't from these domains, so you'd be locked out. Add its domain, or connect one that is.";
const GOOGLE_OFF_SAVE = "Google sign-in is off for your organisation.";

/** The form: the roles in role order, and the domains as typed. */
export interface GoogleForm {
  allowed: Role[];
  only: Role[];
  domains: string;
}

export interface GoogleProblem {
  field: "roles" | "domains";
  message: string;
}

const inOrder = (roles: readonly Role[]): Role[] =>
  ROLE_CHOICES.map((choice) => choice.role).filter((role) => roles.includes(role));

export function googleForm(saved: GoogleRules): GoogleForm {
  return { allowed: inOrder(saved.allowed_roles), only: inOrder(saved.only_roles), domains: saved.domains.join(", ") };
}

/** Commas, spaces or new lines between them; lower case, a leading "@" dropped, each once. */
export function parseDomains(text: string): string[] {
  const found = text
    .split(/[\s,;]+/)
    .map((part) => part.trim().toLowerCase().replace(/^@/, ""))
    .filter((part) => part !== "");
  return [...new Set(found)];
}

// Labels of letters, digits and inner hyphens, at least two, the last all letters.
const DOMAIN = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;
const PERSONAL = new Set([
  "gmail.com",
  "googlemail.com",
  "outlook.com",
  "hotmail.com",
  "live.com",
  "icloud.com",
  "rediffmail.com",
  "proton.me",
  "protonmail.com",
]);
const isPersonal = (domain: string) => PERSONAL.has(domain) || domain.startsWith("yahoo.");

/** The first thing to fix before saving, or null. */
export function googleRulesProblem(form: GoogleForm): GoogleProblem | null {
  if (form.only.some((role) => !form.allowed.includes(role))) return { field: "roles", message: ONLY_NEEDS_ALLOWED };
  const domains = parseDomains(form.domains);
  if (domains.some((domain) => !DOMAIN.test(domain))) return { field: "domains", message: DOMAIN_SHAPE };
  if (domains.some(isPersonal)) return { field: "domains", message: PERSONAL_DOMAIN };
  if (domains.length > MAX_DOMAINS) return { field: "domains", message: TOO_MANY_DOMAINS };
  return null;
}

/** "Google allowed" for one role; taking it away takes "Google only" too. */
export function setAllowed(form: GoogleForm, role: Role, on: boolean): GoogleForm {
  return on
    ? { ...form, allowed: inOrder([...form.allowed, role]) }
    : { ...form, allowed: form.allowed.filter((each) => each !== role), only: form.only.filter((each) => each !== role) };
}

/** "Google only" for one role (its checkbox shows only while Google is allowed). */
export function setOnly(form: GoogleForm, role: Role, on: boolean): GoogleForm {
  return { ...form, only: on ? inOrder([...form.only, role]) : form.only.filter((each) => each !== role) };
}

/** PATCH /admin/settings' google: all three. */
export function googleRulesChange(form: GoogleForm): GoogleRulesChange {
  return { allowed_roles: form.allowed, only_roles: form.only, domains: parseDomains(form.domains) };
}

const sameSet = (a: readonly string[], b: readonly string[]) =>
  a.length === b.length && [...a].sort().join(" ") === [...b].sort().join(" ");

/** The form differs from what is saved (the Save bar shows, and the rules are sent). */
export function googleRulesChanged(saved: GoogleRules, form: GoogleForm): boolean {
  const next = googleRulesChange(form);
  return (
    !sameSet(saved.allowed_roles, next.allowed_roles) ||
    !sameSet(saved.only_roles, next.only_roles) ||
    !sameSet(saved.domains, next.domains)
  );
}

/** A refused save of the rules: the lock-out in the server's words (it knows which of the two),
 * Google switched off by the super admin, else what Settings says. */
export function googleSaveError(caught: unknown): string {
  if (caught instanceof ApiError && caught.code === "google_lockout") return caught.message.trim() || LOCKOUT_NO_LINK;
  if (caught instanceof ApiError && caught.code === "google_off") return GOOGLE_OFF_SAVE;
  return settingsErrorText(caught);
}

/** A refused save that is about the rules, shown in Ways to sign in (by the domains when it is
 * about them); null: the rest of the form's. Only asked when the rules were sent. */
export function googleRefusal(caught: unknown): GoogleProblem | null {
  if (!(caught instanceof ApiError)) return null;
  if (caught.code === "google_lockout" || caught.code === "google_off") {
    return { field: "roles", message: googleSaveError(caught) };
  }
  if (caught.code !== "bad_request") return null;
  const message = googleSaveError(caught);
  return { field: message === ONLY_NEEDS_ALLOWED ? "roles" : "domains", message };
}
