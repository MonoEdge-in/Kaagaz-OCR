// The sign-in's two-step rules, kept pure so vitest can prove them: the 6-digit
// code, the set-up key in groups of 4, when the server asks for the code or for
// setting two-step up, and what to say when it says no. The e-mail and password
// rules are app/login/signIn.ts. One sign-in for everyone (16 Oct 2026): admins
// must have two-step sign-in (MFA required), others sign in as before.

import { ApiError } from "@/lib/api";
import type { TwoStepSetup } from "@/lib/types";
import { NETWORK_FAILURE, SIGN_IN_DOWN, TOO_MANY_ATTEMPTS, normalizeEmail } from "./signIn";

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

/**
 * The server asks for the code (401 code_required) only after the password was
 * right, for anyone with two-step sign-in set up (always admins; #457 phase 3). The screen then shows the
 * code step; nothing else means this.
 */
export function codeNeeded(caught: unknown): boolean {
  return caught instanceof ApiError && caught.status === 401 && caught.code === "code_required";
}

/** After a wrong code, the server's one refusal text, then this. */
export const CODE_ADVICE = "Use the newest code from the app.";

// --- Setting two-step up (MFA required for admins) ---------------------------

export const ENROL_REQUIRED =
  "Set up two-step sign-in to continue. Admins need it to keep your organisation safe.";

/**
 * The set-up offer an admin without two-step sign-in gets after a right password
 * (403 mfa_enrol_required, the key in `details`), else null. Deny by default:
 * anything but exactly the three strings is no offer.
 */
export function enrolOffer(caught: unknown): TwoStepSetup | null {
  if (!(caught instanceof ApiError) || caught.status !== 403 || caught.code !== "mfa_enrol_required") {
    return null;
  }
  const details = caught.details as Partial<Record<keyof TwoStepSetup, unknown>> | null | undefined;
  const { enrol_token, secret, otpauth_uri } = details ?? {};
  if (typeof enrol_token !== "string" || typeof secret !== "string" || typeof otpauth_uri !== "string") {
    return null;
  }
  return { enrol_token, secret, otpauth_uri };
}

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

export const ENROL_FAILED =
  "That code did not work, or this setup has expired. Type the newest code from the app. " +
  "If it still fails, press Start again.";

/** A wrong code and an expired set-up (10 minutes) read the same. */
export function enrolErrorMessage(caught: unknown): string {
  if (caught instanceof ApiError) {
    if (caught.status === 400 || caught.status === 401 || caught.status === 403) return ENROL_FAILED;
    if (caught.status === 429) return TOO_MANY_ATTEMPTS;
    if (caught.status === 0) return NETWORK_FAILURE;
    return SIGN_IN_DOWN;
  }
  // fetch rejects with a TypeError when the network is down or blocked.
  if (caught instanceof TypeError) return NETWORK_FAILURE;
  return SIGN_IN_DOWN;
}
