// Account, Two-step sign-in (#457 phase 3), kept pure so vitest can prove it: which panel
// shows and what it says. Admins keep their code on (the server refuses Turn off); a member
// or reviewer turns it on and off. The panel itself is TwoStepDialog.tsx.

import { ApiError } from "@/lib/api";
import type { Me } from "@/lib/types";
import { setPasswordError } from "./setPassword";

export const MEMBER_OFF =
  "Turn it on and sign-in asks for your password and a 6-digit code from an authenticator app on your phone.";
export const MEMBER_ON = "Sign-in asks for your password and a 6-digit code from your authenticator app.";
export const LOST_CODE = "New phone, or lost it? Your admin can reset your two-step code.";
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

/** "admin": always on, nothing to change; "on" or "off": a member's or reviewer's own choice. */
export function twoStepView(me: Me): "admin" | "on" | "off" {
  if (me.user.role === "admin") return "admin";
  return me.user.totp_enabled ? "on" : "off";
}

/** The server's 400 and 409 say what is wrong in plain words; the rest as Change password says it. */
export function twoStepError(caught: unknown): string {
  if (caught instanceof ApiError && (caught.status === 400 || caught.status === 409) && caught.message.trim()) {
    return caught.message.trim();
  }
  if (caught instanceof ApiError && caught.status === 401 && caught.code !== "bad_credentials") return SIGNED_OUT;
  return setPasswordError(caught).message;
}
