// An admin's own two-step sign-in, turned off or on from their Account menu (Tushar,
// 30 Oct 2026), kept pure so vitest can prove it: who sees the item, on or off, the
// dialog's words, and what to say when the server says no. With it off the admin signs
// in with e-mail and password only, until they turn it on again. Members and reviewers
// have no codes, so the item is not there for them. The code and set-up key rules are
// app/login/twoStep.ts; the dialog is TwoStepDialog.tsx.

import { NETWORK_FAILURE, TOO_MANY_ATTEMPTS } from "@/app/login/signIn";
import { ApiError } from "@/lib/api";
import type { Me } from "@/lib/types";
import { sessionScope } from "./gate";

export type TwoStepState = "on" | "off";

/** Deny by default, like gate.ts: only true is on. */
export function twoStepState(me: Me): TwoStepState {
  return me.user.totp_enabled === true ? "on" : "off";
}

/** The item is for an admin's own admin session only: the server's routes need one. */
export function showsTwoStepItem(me: Me): boolean {
  return me.user.role === "admin" && sessionScope(me) === "admin";
}

export const TWO_STEP_ACCOUNT = {
  item: "Two-step sign-in",
  password: "Password",
  code: "6-digit code",
  offWarning: "Anyone who learns your password can then sign in as you.",
  offButton: "Turn off two-step sign-in",
  offDone: "Two-step sign-in is off. You'll sign in with your email and password only.",
  onLead: "Type your password to see the key for your authenticator app.",
  onButton: "Continue",
  onDone: "Two-step sign-in is on.",
  cancel: "Cancel",
  done: "Done",
  passwordMissing: "Enter your password.",
  failed: "That did not work. Try again in a minute.",
} as const;

/** What the dialog says first, and the one button that changes it. */
export function statusText(state: TwoStepState): { text: string; action: string } {
  return state === "on"
    ? {
        text: "Two-step sign-in is on. You type a 6-digit code from your authenticator app after your password.",
        action: "Turn off",
      }
    : { text: "Two-step sign-in is off.", action: "Turn on" };
}

/** The password exactly as typed: never trimmed. */
export function passwordError(value: string): string | null {
  return value === "" ? TWO_STEP_ACCOUNT.passwordMissing : null;
}

export type Field = "password" | "code";

/**
 * Where a refusal goes and what it says. A 400 names a wrong or missing password or
 * code in plain words, so it is shown as sent, on the field it names (the code only when
 * the form has one and the words say code, not password). A 403 or 409 (already off) is
 * shown as sent above the buttons. A server error's own text never is.
 */
export function refusal(caught: unknown, hasCode: boolean): { field: Field | null; message: string } {
  if (caught instanceof ApiError) {
    const message = caught.message.trim() || TWO_STEP_ACCOUNT.failed;
    if (caught.status === 400) {
      const saysCode = hasCode && /\bcode\b/i.test(message) && !/password/i.test(message);
      return { field: saysCode ? "code" : "password", message };
    }
    if (caught.status === 403 || caught.status === 409) return { field: null, message };
    if (caught.status === 429) return { field: null, message: TOO_MANY_ATTEMPTS };
    if (caught.status === 0) return { field: null, message: NETWORK_FAILURE };
    return { field: null, message: TWO_STEP_ACCOUNT.failed };
  }
  // fetch rejects with a TypeError when the network is down or blocked.
  if (caught instanceof TypeError) return { field: null, message: NETWORK_FAILURE };
  return { field: null, message: TWO_STEP_ACCOUNT.failed };
}
