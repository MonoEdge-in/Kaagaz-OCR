// The sign-up form's rules (16 Oct 2026), kept pure so vitest can prove them:
// what each field must hold (the server's own limits), which field gets focus
// after a failed submit, and what to say when the server says no.

import { MIN_PASSWORD_LENGTH } from "@/components/admin/logic";
import { ApiError } from "@/lib/api";
import type { SignUpConfirming, TwoStepSetup } from "@/lib/types";
import { NETWORK_FAILURE, normalizeEmail } from "@/app/login/signIn";

export type SignUpField = "organisation" | "name" | "email" | "password";

export type SignUpInput = Record<SignUpField, string>;

export type SignUpErrors = Record<SignUpField, string | null>;

/** The fields in screen order. */
export const SIGN_UP_FIELDS: readonly SignUpField[] = ["organisation", "name", "email", "password"];

// The server's limits (routers/auth.py SignupBody).
export const MAX_ORGANISATION = 200;
export const MAX_NAME = 100;
export const MAX_EMAIL = 254;

export const ORGANISATION_MISSING = "Enter your organisation's name.";
export const NAME_MISSING = "Enter your name.";
export const EMAIL_MISSING = "Enter your work e-mail address.";
export const EMAIL_INVALID = "Enter a valid e-mail address, like name@company.com.";
export const PASSWORD_TOO_SHORT = `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
export const PASSWORD_HINT = `At least ${MIN_PASSWORD_LENGTH} characters.`;

// As the server's check: something, an @, something with a dot, no spaces.
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function signUpFieldError(field: SignUpField, value: string): string | null {
  if (field === "organisation") return value.trim() === "" ? ORGANISATION_MISSING : null;
  if (field === "name") return value.trim() === "" ? NAME_MISSING : null;
  if (field === "email") {
    const email = normalizeEmail(value);
    if (email === "") return EMAIL_MISSING;
    return EMAIL_SHAPE.test(email) ? null : EMAIL_INVALID;
  }
  // A password is sent exactly as typed: never trimmed.
  return value.length < MIN_PASSWORD_LENGTH ? PASSWORD_TOO_SHORT : null;
}

export function validateSignUp(input: SignUpInput): SignUpErrors {
  return {
    organisation: signUpFieldError("organisation", input.organisation),
    name: signUpFieldError("name", input.name),
    email: signUpFieldError("email", input.email),
    password: signUpFieldError("password", input.password),
  };
}

/** The first field, in screen order, that needs fixing; focus goes there. */
export function firstInvalidSignUp(errors: SignUpErrors): SignUpField | null {
  return SIGN_UP_FIELDS.find((field) => errors[field] !== null) ?? null;
}

/** What the server is sent: names and e-mail trimmed, the password as typed. */
export function signUpBody(input: SignUpInput) {
  return {
    organisation_name: input.organisation.trim(),
    name: input.name.trim(),
    email: normalizeEmail(input.email),
    password: input.password,
  };
}

/** With email on (#457): the email is confirmed first, then the organisation waits for an operator. */
export const CHECK_EMAIL =
  "Check your email to confirm your address. Your organisation will then wait for approval.";

export function signUpConfirming(reply: TwoStepSetup | SignUpConfirming): reply is SignUpConfirming {
  return "status" in reply && reply.status === "confirm_email";
}

export const ACCOUNT_EXISTS = "An account with this e-mail already exists. Sign in instead.";
export const TOO_MANY_SIGN_UPS = "Too many sign-ups from here. Try again in an hour.";
export const SIGN_UP_REFUSED = "Check the details you typed, then try again.";
export const SIGN_UP_DOWN = "Creating an account is not working right now. Try again in a minute.";
export const SIGN_UP_OFF = "Creating an account here is turned off. Ask your account admin to add you.";

export interface SignUpError {
  message: string;
  /** True when the e-mail already has an account: the banner links to sign-in. */
  signInInstead: boolean;
}

/** The banner text for a failed sign-up. */
export function signUpError(caught: unknown): SignUpError {
  const say = (message: string, signInInstead = false) => ({ message, signInInstead });
  if (caught instanceof ApiError) {
    if (caught.status === 409) return say(ACCOUNT_EXISTS, true);
    if (caught.status === 429) return say(TOO_MANY_SIGN_UPS);
    if (caught.status === 404) return say(SIGN_UP_OFF);
    if (caught.status === 400) return say(SIGN_UP_REFUSED);
    if (caught.status === 0) return say(NETWORK_FAILURE);
    return say(SIGN_UP_DOWN);
  }
  // fetch rejects with a TypeError when the network is down or blocked.
  if (caught instanceof TypeError) return say(NETWORK_FAILURE);
  return say(SIGN_UP_DOWN);
}
