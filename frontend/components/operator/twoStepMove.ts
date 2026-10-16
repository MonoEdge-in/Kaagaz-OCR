// Moving the operator's own two-step to a new device (Tushar, 16 Oct 2026: a
// handover to another person's phone). Kept pure so vitest can prove it: the
// words, the checks before sending, when the dialog may close, and what to say
// when the server says no. Two-step is never off: the current device works
// until the new one's first code is accepted, then only the new one does.

import { ApiError } from "@/lib/api";
import { operatorErrorMessage } from "./logic";

export type MoveStep = "check" | "scan" | "done";

export const MOVE_TEXT = {
  title: "Move two-step to a new device",
  intro:
    "For a handover: the new device gets a new key. Your current device keeps working until the new one is confirmed. Two-step stays on the whole time.",
  password: "Your password",
  currentCode: "6-digit code from your current device",
  currentHint: "Use a new code: the one you signed in with does not work again.",
  showKey: "Show the new key",
  scanTitle: "Set up the new device",
  scanSteps: [
    "On the new device, open Google Authenticator or Microsoft Authenticator and add an account.",
    "Scan the QR code, or choose to enter a setup key and type the key shown here.",
    "Type the 6-digit code it now shows, then press Move two-step.",
  ],
  qrLabel: "QR code to scan with the new device's authenticator app",
  noCamera: "No camera to scan with? Type the key below instead.",
  keyPrivate: "Keep this key private. Anyone who has it can make your codes.",
  newCode: "Enter the 6-digit code from the new device",
  move: "Move two-step",
  moving: "Moving…",
  done: "Two-step moved. The new device is now the only one that works. Other browsers were signed out.",
  doneButton: "Done",
  cancel: "Cancel",
  keepGoing: "Keep going",
  closeAnyway: "Close without moving",
} as const;

export const PASSWORD_MISSING = "Enter your password.";

/** The password exactly as typed: never trimmed. */
export function passwordError(value: string): string | null {
  return value === "" ? PASSWORD_MISSING : null;
}

/** Something would be lost by closing: typed input, or a new key already shown. */
export function hasUnsaved(step: MoveStep, typed: { password: string; code: string }): boolean {
  if (step === "done") return false;
  return step === "scan" || typed.password !== "" || typed.code !== "";
}

/** The question asked before closing with something unsaved, for this step. */
export function leaveQuestion(step: MoveStep): string {
  return step === "scan"
    ? "Close without moving? Two-step stays on your current device, and the key shown here will not be used."
    : "Close without moving? What you typed will be cleared. Two-step stays on your current device.";
}

/**
 * The Close button, Esc or the backdrop was used: never while a request runs
 * ("stay"); with something unsaved, ask first (the dialog shows leaveQuestion,
 * with Keep going focused); else close.
 */
export function closeAsked(state: { busy: boolean; unsaved: boolean }): "stay" | "ask" | "close" {
  if (state.busy) return "stay";
  return state.unsaved ? "ask" : "close";
}

export const START_REFUSED =
  "That password or code was not accepted. Check both, wait for a new code, and try again. After many wrong tries, this stops for a while.";
export const CONFIRM_REFUSED =
  "That code from the new device was not accepted, so nothing changed. Wait for its next code and try again, or close and start again.";

/** Plain words for a refused step; anything else as the other operator screens say it. */
export function moveErrorMessage(caught: unknown, step: "check" | "scan"): string {
  if (caught instanceof ApiError && caught.code === "move_refused") return step === "check" ? START_REFUSED : CONFIRM_REFUSED;
  return operatorErrorMessage(caught);
}
