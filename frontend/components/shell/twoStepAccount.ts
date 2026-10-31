// Account, Two-step sign-in (#457 phase 3), kept pure so vitest can prove it: which panel
// shows and what it says. Anyone turns it on and off, an admin too (Tushar, 30 Oct 2026):
// an admin's then stays off until they turn it on again, and they sign in with e-mail and
// password only. The panel itself is TwoStepDialog.tsx.

import { ApiError } from "@/lib/api";
import type { Me } from "@/lib/types";
import { setPasswordError } from "./setPassword";

export const OFF_TEXT =
  "Turn it on and sign-in asks for your password and a 6-digit code from an authenticator app on your phone.";
export const ON_TEXT = "Sign-in asks for your password and a 6-digit code from your authenticator app.";
export const LOST_CODE = "New phone, or lost it? Your admin can reset your two-step code.";
export const OFF_WARNING = "Anyone who learns your password can then sign in as you.";
export const TURNED_ON = "Two-step sign-in is on.";
export const TURNED_OFF = "Two-step sign-in is off. Your other devices are signed out.";
const SIGNED_OUT = "Your session has ended. Sign in again, then try again.";
// The server's words for a wrong password, and a missing or wrong code (routers/auth.py).
export const PASSWORD_NOT_RIGHT = "Your current password is not right.";
const CODE_REFUSALS = [
  "Enter the 6-digit code from your authenticator app.",
  "That code is not right. Wait for the next code and try again.",
];

/** The field a refusal belongs on (#457 review): a wrong password, or a missing or wrong code;
 * null for anything else, which is said under the form. */
export function proofField(caught: unknown): "password" | "code" | null {
  if (!(caught instanceof ApiError) || caught.status !== 400) return null;
  const said = caught.message.trim();
  if (said === PASSWORD_NOT_RIGHT) return "password";
  return CODE_REFUSALS.includes(said) ? "code" : null;
}

/** "on" or "off", the same for every role. Deny by default, like gate.ts: only true is on. */
export function twoStepView(me: Me): "on" | "off" {
  return me.user.totp_enabled === true ? "on" : "off";
}

/** The server's 400 and 409 say what is wrong in plain words; the rest as Change password says it. */
export function twoStepError(caught: unknown): string {
  if (caught instanceof ApiError && (caught.status === 400 || caught.status === 409) && caught.message.trim()) {
    return caught.message.trim();
  }
  if (caught instanceof ApiError && caught.status === 401 && caught.code !== "bad_credentials") return SIGNED_OUT;
  return setPasswordError(caught).message;
}
