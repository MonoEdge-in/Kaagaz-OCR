// Locked files (PRD 7 section 6, PR 13): the words and the small rules of opening one, locking
// and unlocking, and the give pop-up, kept pure so they are unit tested in node
// (tests/locked-files.test.ts). A locked file opens only as page pictures, after the person's
// own Kaagaz password (or a fresh Google sign-in for a person who signs in with Google only),
// for 10 minutes in this browser; it is never downloaded, printed or read as text.

import { ApiError } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import type { LockRefusal, PlaceLockedFile } from "@/lib/types";

export const LOCKED_TEXT = {
  heading: "This file is locked",
  prompt:
    "Enter your password to open locked files. This keeps locked files safe on shared computers. They stay open for 10 minutes.",
  googlePrompt:
    "Confirm with Google to open locked files. This keeps locked files safe on shared computers. They stay open for 10 minutes.",
  password: "Your Kaagaz password",
  open: "Open",
  opening: "Opening…",
  google: "Continue with Google",
  goingToGoogle: "Opening Google…",
  checking: "Checking…",
  checkFailed: "Kaagaz couldn't check whether locked files are open. Try again.",
  preparing: "This file is still being prepared. Its pages show here once they are ready.",
  inTrash: "This file is in the Trash. Restore it first.",
  pageFailed: "This page couldn't be shown. Try again.",
  viewOnly: "View only: locked files can't be downloaded, printed or copied.",
  previous: "Previous page",
  next: "Next page",
  tryAgain: "Try again",
} as const;

const OFFLINE = "Check your internet connection, then try again.";

/** What the Google confirm came back with (?open_error=), in plain words; null for none. */
export function openErrorText(code: string | null): string | null {
  if (code === null || code === "") return null;
  if (code === "not_fresh") return "Sign in with Google again to open locked files.";
  if (code === "not_yours") return "That Google account isn't the one connected to you.";
  return "Google couldn't confirm it was you. Try again.";
}

/** Why the password did not open locked files: the server's words, and when to try again after
 * a block (its Retry-After); `google` when this person confirms with Google instead. */
export function openFailure(caught: unknown, now: number): { message: string; google: boolean } {
  if (!(caught instanceof ApiError)) return { message: OFFLINE, google: false };
  if (caught.code === "google_confirm_required") return { message: "", google: true };
  if (caught.status === 429 && caught.retryAfter !== undefined) {
    return { message: `${caught.message} You can try again at ${clockTime(now + caught.retryAfter * 1000)}.`, google: false };
  }
  return { message: caught.message, google: false };
}

const clockTime = (at: number) => new Date(at).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });

/** Whole seconds left of the 10 minutes (never below 0). */
export function secondsLeft(until: string, now: number): number {
  return Math.max(0, Math.ceil((Date.parse(until) - now) / 1000));
}

/** "Open for 9:05": the countdown beside the pages. */
export function openForText(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `Open for ${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

/** "Page 2 of 5". */
export const pageOfText = (index: number, total: number) => `Page ${index + 1} of ${total}`;

/** The page's turn for everyone (PRD 7 section 14), as a CSS rotation. */
export const turnStyle = (turn: number) => (turn === 0 ? undefined : { transform: `rotate(${turn}deg)` });

// --- locking and unlocking (Admins) ---

/** The lock dialog's title: one file by its name, or a selection by its count. */
export const lockTitle = (names: readonly string[]) =>
  names.length === 1 ? `Lock "${names[0]}"?` : `Lock ${formatNumber(names.length)} files?`;

export function lockExplainText(count: number): string {
  const which = count === 1 ? "this file" : "these files";
  return (
    "Locked files are kept out of Ask, search inside files and totals, for everyone, including you. " +
    `People open them directly after entering their Kaagaz password. Dashboard totals will no longer count ${which}.`
  );
}

export const LOCK_TEXT = {
  photograph: "People who can open it can still photograph the screen.",
  who: "Who can still open it:",
  loadingPeople: "Loading who can see it…",
  password: "Your password",
  lock: (count: number) => (count === 1 ? "Lock file" : `Lock ${formatNumber(count)} files`),
  locking: "Locking…",
  cancel: "Cancel",
  menu: "Lock",
  menuHint: "Keep it out of Ask and open it only with a password",
  unlockMenu: "Unlock",
  unlockTitle: (name: string) => `Unlock "${name}"?`,
  unlockWarning: "Everyone who can see its folder can open it, and Ask can use it again.",
  unlock: "Unlock",
  unlocking: "Unlocking…",
  unlocked: "File unlocked.",
} as const;

/** "File locked." or "8 files locked." */
export const lockedMessage = (count: number) => (count === 1 ? "File locked." : `${formatNumber(count)} files locked.`);

// Why a bulk lock refused a file (PR 12's outcomes), after the file's name.
const REFUSALS: Record<string, string> = {
  not_lockable: "can't be locked yet. Keep it in a private folder instead.",
  kind: "can't be locked yet. Keep it in a private folder instead.",
  no_pictures: "can't be locked yet. Keep it in a private folder instead.",
  already_locked: "is locked already.",
  in_trash: "is in the Trash.",
  not_found: "couldn't be found.",
};

/** One line a refused file: "Prices.xlsx can't be locked yet. Keep it in a private folder instead." */
export function refusedLines(refused: readonly LockRefusal[], names: ReadonlyMap<string, string>): string[] {
  return refused.map(
    ({ id, reason }) => `${names.get(id) ?? "A file"} ${REFUSALS[reason] ?? "couldn't be locked."}`,
  );
}

/** The refused files of a bulk lock's 409 (its details), else none. */
export function refusalsOf(caught: unknown): LockRefusal[] {
  if (!(caught instanceof ApiError) || caught.status !== 409) return [];
  const details = caught.details as { refused?: unknown } | undefined;
  return Array.isArray(details?.refused) ? (details.refused as LockRefusal[]) : [];
}

/** A failed lock or unlock in words: the server's own (wrong password, not lockable, in Trash). */
export const lockFailureText = (caught: unknown) => (caught instanceof ApiError ? caught.message : OFFLINE);

// --- the give pop-up (giving a place that holds locked files) ---

/** A search box shows past this many locked files. */
export const SEARCH_FROM = 10;

const files = (count: number) => `${formatNumber(count)} ${count === 1 ? "file" : "files"}`;

/** "Give Ravi access to Land & Legal. Land & Legal has 120 files. 5 are locked. Choose which
 * locked files Ravi can open:" */
export function giveIntroText(person: string, place: string, total: number, locked: number): string {
  const intro = `Give ${person} access to ${place}. ${place} has ${files(total)}.`;
  if (locked === 0) return `${intro} None are locked.`;
  const are = locked === 1 ? "1 is locked" : `${formatNumber(locked)} are locked`;
  return `${intro} ${are}. Choose which locked files ${person} can open:`;
}

/** "3 of 5 chosen". */
export const chosenText = (chosen: number, of: number) => `${formatNumber(chosen)} of ${formatNumber(of)} chosen`;

/** With none ticked: "Ravi will get the 115 other files, but none of the locked ones." */
export const noneChosenText = (person: string, others: number) =>
  `${person} will get the ${formatNumber(others)} other ${others === 1 ? "file" : "files"}, but none of the locked ones.`;

/** "Sale deed – Survey 72.pdf (Land & Legal / Daregaon)", or the name alone at the place's top. */
export const lockedFileLabel = (file: Pick<PlaceLockedFile, "name" | "path">) => (file.path ? `${file.name} (${file.path})` : file.name);

/** The files whose name or path holds `query` (any case); all of them for an empty query. */
export function matchingFiles(items: readonly PlaceLockedFile[], query: string): PlaceLockedFile[] {
  const wanted = query.trim().toLowerCase();
  return wanted === "" ? [...items] : items.filter((file) => lockedFileLabel(file).toLowerCase().includes(wanted));
}
