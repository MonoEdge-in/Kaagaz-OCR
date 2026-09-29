// The set-new-password screen's rules (PRD A5), kept pure so vitest can prove
// them. Someone who signed in with a temporary password (an admin made the
// account or reset the password) must choose their own before anything else.
// Passwords are compared exactly as typed: never trimmed.

import { MIN_PASSWORD_LENGTH } from "@/components/admin/logic";
import { ApiError } from "@/lib/api";

export interface NewPasswordInput {
  current: string;
  next: string;
  again: string;
}

export type NewPasswordField = keyof NewPasswordInput;

export type NewPasswordErrors = Partial<Record<NewPasswordField, string>>;

export const NEW_PASSWORD_FIELD_IDS: Record<NewPasswordField, string> = {
  current: "set-password-current",
  next: "set-password-new",
  again: "set-password-again",
};

export const CURRENT_MISSING = "Enter the password you signed in with.";
export const NEXT_TOO_SHORT = `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
export const NEXT_SAME = "Choose a password that is different from the one you signed in with.";
export const AGAIN_MISMATCH = "The two new passwords are not the same. Type the new one again.";

/** Messages per field; an empty object means the form may be sent. */
export function validateNewPassword(input: NewPasswordInput): NewPasswordErrors {
  const errors: NewPasswordErrors = {};
  if (input.current === "") errors.current = CURRENT_MISSING;
  if (input.next.length < MIN_PASSWORD_LENGTH) errors.next = NEXT_TOO_SHORT;
  else if (input.next === input.current) errors.next = NEXT_SAME;
  if (input.again !== input.next) errors.again = AGAIN_MISMATCH;
  return errors;
}

/** The first field (in screen order) with a problem, for focus; null if none. */
export function firstInvalidNewPassword(errors: NewPasswordErrors): NewPasswordField | null {
  const order: NewPasswordField[] = ["current", "next", "again"];
  return order.find((key) => errors[key] !== undefined) ?? null;
}

export const CURRENT_WRONG =
  "The password you signed in with is not right. Check it and try again. " +
  "Too many wrong tries lock the account for a while.";
export const SESSION_ENDED = "Your session has ended. Sign in again to set your new password.";
export const NEW_REFUSED =
  `The new password was not accepted. Use at least ${MIN_PASSWORD_LENGTH} characters, ` +
  "then try again.";
// The same words as the sign-in page (app/login/signIn.ts; the test holds them equal).
const TOO_MANY_ATTEMPTS = "Too many attempts. Wait a minute, then try again.";
const NETWORK_FAILURE = "Could not reach Kaagaz. Check your internet connection and try again.";
const SIGN_IN_DOWN = "Sign-in is not working right now. Try again in a minute.";

export interface SetPasswordError {
  message: string;
  /** True when the session is gone and the person must sign in again. */
  signedOut: boolean;
}

/**
 * The banner text for a failed change. The route's 400s name the problem in
 * plain words (a wrong current password, a lock after too many, the same
 * password again), so they are shown as sent; errors.py keeps every message
 * safe to show. A 401 means the session itself has ended. A server error's
 * text is never shown.
 */
export function setPasswordError(caught: unknown): SetPasswordError {
  const say = (message: string, signedOut = false) => ({ message, signedOut });
  if (caught instanceof ApiError) {
    if (caught.code === "bad_credentials") return say(CURRENT_WRONG);
    if (caught.status === 401) return say(SESSION_ENDED, true);
    if (caught.status === 400) return say(caught.message.trim() || NEW_REFUSED);
    if (caught.status === 429) return say(TOO_MANY_ATTEMPTS);
    if (caught.status === 0) return say(NETWORK_FAILURE);
    return say(SIGN_IN_DOWN);
  }
  if (caught instanceof TypeError) return say(NETWORK_FAILURE);
  return say(SIGN_IN_DOWN);
}
