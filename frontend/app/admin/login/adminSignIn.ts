// The admin sign-in's rules, kept pure so vitest can prove them: the 6-digit
// code, the set-up key in groups of 4, and what to say when the server says
// no. The e-mail and password rules are the normal sign-in's (app/login).

import { ApiError } from "@/lib/api";
import {
  NETWORK_FAILURE,
  SIGN_IN_DOWN,
  TOO_MANY_ATTEMPTS,
  emailError,
  normalizeEmail,
  passwordError,
} from "@/app/login/signIn";

// --- The code --------------------------------------------------------------

export const CODE_MISSING = "Enter the 6-digit code from your authenticator app.";
export const CODE_INVALID = "The code is 6 digits, each 0 to 9. Type it again from the app.";

// ASCII digits only, like the server (^[0-9]{6}$). Devanagari (०-९) or
// fullwidth digits are refused here with a clear message, not sent.
const CODE_SHAPE = /^[0-9]{6}$/;

/** Apps show the code as "123 456"; the spaces are never part of it. */
export function normalizeCode(value: string): string {
  return value.replace(/\s+/g, "");
}

export function codeError(value: string): string | null {
  const code = normalizeCode(value);
  if (code === "") return CODE_MISSING;
  return CODE_SHAPE.test(code) ? null : CODE_INVALID;
}

// --- The first screen: e-mail, password and (once set up) the code ------------

export type AdminSignInField = "email" | "password" | "code";

export interface AdminSignInErrors {
  email: string | null;
  password: string | null;
  code: string | null;
}

/**
 * The code may be left empty: an admin who has not turned on two-step sign-in
 * signs in with the password alone. A typed code is checked.
 */
export function optionalCodeError(value: string): string | null {
  return normalizeCode(value) === "" ? null : codeError(value);
}

export function validateAdminSignIn(
  email: string,
  password: string,
  code: string,
): AdminSignInErrors {
  return {
    email: emailError(email),
    password: passwordError(password),
    code: optionalCodeError(code),
  };
}

/** The first field, in form order, that needs fixing; focus goes there. */
export function firstInvalidAdmin(errors: AdminSignInErrors): AdminSignInField | null {
  if (errors.email) return "email";
  if (errors.password) return "password";
  if (errors.code) return "code";
  return null;
}

// --- The set-up step (EnrolStep, in Admin, Settings) ------------------------

export const ENROL_ISSUER = "Kaagaz";

export const ENROL_STEPS: readonly string[] = [
  "Open Google Authenticator or Microsoft Authenticator on your phone and add an account.",
  "Scan the QR code, or choose to enter a setup key and type the account and key shown here.",
  "Type the 6-digit code the app now shows, then press Finish setup.",
];

/** What the app asks for when a key is typed in by hand. */
export function enrolDetails(email: string): { label: string; value: string }[] {
  return [
    { label: "Issuer", value: ENROL_ISSUER },
    { label: "Account", value: normalizeEmail(email) },
    { label: "Type", value: "Time based" },
  ];
}

// Base32 has no spaces or hyphens, so both are only grouping.
const KEY_SEPARATORS = /[\s-]+/g;

/** "JBSWY3DP…" as "JBSW Y3DP …": easier to type and to check. */
export function groupSecret(secret: string): string {
  const key = secretForCopy(secret);
  return key.match(/.{1,4}/g)?.join(" ") ?? "";
}

/** The key as one word in capitals, for the clipboard. */
export function secretForCopy(secret: string): string {
  return secret.replace(KEY_SEPARATORS, "").toUpperCase();
}

// --- What to say when the server says no ------------------------------------

// PRD A3: the server sends one 401 text for a wrong password, an unknown
// e-mail, a locked account and a wrong code, with the lock time filled in from
// LOGIN_LOCK_MINUTES. The screen shows it as sent, then this advice.
export const ADMIN_SIGN_IN_ADVICE = "Check your e-mail, password and the newest code from the app.";

// Any other refusal, or a 401 without the server's text. It names no lock
// time: only the server knows the setting.
export const ADMIN_SIGN_IN_FAILED =
  `Email or password is wrong, or the account is locked. ${ADMIN_SIGN_IN_ADVICE}`;

export const CHANGE_PASSWORD_FIRST =
  "Set a new password first. Sign in on the normal sign-in page, choose your new password, " +
  "then come back here.";

export interface AdminSignInError {
  message: string;
  /** True when the account still has a temporary password (403 password_change_required). */
  changePasswordFirst: boolean;
}

/**
 * The banner text for a failed admin sign-in. A 401 bad_credentials shows the
 * server's own text (the same for every reason) and the advice. Every other
 * refusal (400, 401, 403) gets ADMIN_SIGN_IN_FAILED, whatever the server's code
 * or message, so the screen never tells which part was wrong or that an
 * account is not an admin. The one exception is the temporary-password case,
 * which the server tells apart on purpose (plan 9.5).
 */
export function adminSignInError(caught: unknown): AdminSignInError {
  if (caught instanceof ApiError && caught.code === "password_change_required") {
    return { message: CHANGE_PASSWORD_FIRST, changePasswordFirst: true };
  }
  if (caught instanceof ApiError && caught.status === 401 && caught.code === "bad_credentials") {
    const said = caught.message.trim();
    const message = said ? `${said} ${ADMIN_SIGN_IN_ADVICE}` : ADMIN_SIGN_IN_FAILED;
    return { message, changePasswordFirst: false };
  }
  return { message: refusalMessage(caught, ADMIN_SIGN_IN_FAILED), changePasswordFirst: false };
}

export const ENROL_FAILED =
  "That code did not work, or this setup has expired. Type the newest code from the app. " +
  "If it still fails, press Start again.";

/** A wrong code and an expired set-up (10 minutes) read the same. */
export function enrolErrorMessage(caught: unknown): string {
  return refusalMessage(caught, ENROL_FAILED);
}

function refusalMessage(caught: unknown, refused: string): string {
  if (caught instanceof ApiError) {
    if (caught.status === 400 || caught.status === 401 || caught.status === 403) return refused;
    if (caught.status === 429) return TOO_MANY_ATTEMPTS;
    if (caught.status === 0) return NETWORK_FAILURE;
    return SIGN_IN_DOWN;
  }
  // fetch rejects with a TypeError when the network is down or blocked.
  if (caught instanceof TypeError) return NETWORK_FAILURE;
  return SIGN_IN_DOWN;
}
