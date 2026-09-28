// The sign-in form's rules, kept pure so vitest can prove them: what each
// field must hold, which field gets focus after a failed submit, and what to
// say when the server says no. Messages say what to do next.

import { ApiError } from "@/lib/api";

export type SignInField = "email" | "password";

export interface SignInErrors {
  email: string | null;
  password: string | null;
}

export const EMAIL_MISSING = "Enter your e-mail address.";
export const EMAIL_INVALID = "Enter an e-mail address with an @, like the one you use for work.";
export const PASSWORD_MISSING = "Enter your password.";

// Something, an @, something, with no spaces. The server decides the rest.
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+$/;

/** Spaces around an e-mail address are a slip, never part of it. */
export function normalizeEmail(value: string): string {
  return value.trim();
}

export function emailError(value: string): string | null {
  const email = normalizeEmail(value);
  if (email === "") return EMAIL_MISSING;
  return EMAIL_SHAPE.test(email) ? null : EMAIL_INVALID;
}

/** A password is sent exactly as typed: never trimmed, any characters allowed. */
export function passwordError(value: string): string | null {
  return value === "" ? PASSWORD_MISSING : null;
}

export function validateSignIn(email: string, password: string): SignInErrors {
  return { email: emailError(email), password: passwordError(password) };
}

/** The first field, in form order, that needs fixing; focus goes there. */
export function firstInvalid(errors: SignInErrors): SignInField | null {
  if (errors.email) return "email";
  if (errors.password) return "password";
  return null;
}

export const WRONG_DETAILS_ADVICE = "Check your e-mail and password, then try again.";
export const TOO_MANY_ATTEMPTS = "Too many attempts. Wait a minute, then try again.";
export const NETWORK_FAILURE = "Could not reach Kaagaz. Check your internet connection and try again.";
export const SIGN_IN_DOWN = "Sign-in is not working right now. Try again in a minute.";

/**
 * The banner text for a failed sign-in. A 401 shows the backend's own message
 * (it is the same for an unknown e-mail, a wrong password and a disabled
 * account, so it reveals nothing) followed by what to do.
 */
export function signInErrorMessage(caught: unknown): string {
  if (caught instanceof ApiError) {
    if (caught.status === 401) {
      const said = caught.message.trim();
      return said ? `${said} ${WRONG_DETAILS_ADVICE}` : WRONG_DETAILS_ADVICE;
    }
    if (caught.status === 429) return TOO_MANY_ATTEMPTS;
    if (caught.status === 0) return NETWORK_FAILURE;
    return SIGN_IN_DOWN;
  }
  // fetch rejects with a TypeError when the network is down or blocked.
  if (caught instanceof TypeError) return NETWORK_FAILURE;
  return SIGN_IN_DOWN;
}
