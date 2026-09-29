// The signed-in admin's own two-step sign-in (2FA), kept pure so vitest can
// prove it: On or Off, the password to turn it off, and what to say when the
// server says no. The code and set-up key rules are app/admin/login/adminSignIn.ts.

import { ApiError } from "@/lib/api";
import type { Me } from "@/lib/types";

export type TwoStepState = "on" | "off";

/** Deny by default, like components/shell/gate.ts: only true is On. */
export function twoStepState(me: Me): TwoStepState {
  return me.user.totp_enabled === true ? "on" : "off";
}

export const TWO_STEP_TEXT: Record<TwoStepState, { label: string; meaning: string }> = {
  on: {
    label: "On",
    meaning: "Admin sign-in asks for your password and a 6-digit code from your authenticator app.",
  },
  off: { label: "Off", meaning: "Admin sign-in asks for your password only." },
};

export const TURN_OFF_QUESTION =
  "Turn off two-step sign-in? Admin sign-in will ask for your password only, and your other " +
  "browsers will be signed out.";

export const PASSWORD_MISSING = "Enter your current password.";

/** The current password, exactly as typed: never trimmed. */
export function turnOffPasswordError(value: string): string | null {
  return value === "" ? PASSWORD_MISSING : null;
}

export const SESSION_ENDED = "Your admin session has ended. Sign in again on the admin screen.";
export const TWO_STEP_FAILED = "That did not work. Try again in a minute.";
// The same words as the sign-in page (app/login/signIn.ts; the test holds them equal).
const TOO_MANY_ATTEMPTS = "Too many attempts. Wait a minute, then try again.";
const NETWORK_FAILURE = "Could not reach Kaagaz. Check your internet connection and try again.";

/**
 * Turning on or off failed. The 400s and 409s name the problem in plain words
 * (a wrong password, a lock, already on or off), so they are shown as sent;
 * errors.py keeps every message safe to show. A server error's text never is.
 */
export function twoStepError(caught: unknown): string {
  if (caught instanceof ApiError) {
    if (caught.status === 400 || caught.status === 409) return caught.message.trim() || TWO_STEP_FAILED;
    if (caught.status === 401 || caught.status === 403) return SESSION_ENDED;
    if (caught.status === 429) return TOO_MANY_ATTEMPTS;
    if (caught.status === 0) return NETWORK_FAILURE;
    return TWO_STEP_FAILED;
  }
  if (caught instanceof TypeError) return NETWORK_FAILURE;
  return TWO_STEP_FAILED;
}
