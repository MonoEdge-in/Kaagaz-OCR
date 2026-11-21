// Locked files (PRD 7 section 6, PR 13): the words and the small rules of opening one, locking
// and unlocking, kept pure so they are unit tested in node (tests/locked-open.test.ts). A locked
// file opens only as page pictures, after a fresh company sign-in, for 10 minutes in this browser; it is never
// downloaded, printed or read as text.

import type { CSSProperties } from "react";
import { ApiError } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { pageAspect } from "@/lib/regions";
import type { LockRefusal, PageSummary } from "@/lib/types";

export const LOCKED_TEXT = {
  heading: "This file is locked",
  open: "Open",
  checking: "Checking…",
  checkFailed: "Kaagaz couldn't check whether locked files are open. Try again.",
  preparing: "This file is still being prepared. Its pages show here once they are ready.",
  inTrash: "This file is in the Trash. Restore it first.",
  pageFailed: "This page couldn't be shown.",
  viewOnly: "View only: locked files can't be downloaded, printed or copied.",
  previous: "Previous page",
  next: "Next page",
  tryAgain: "Try again",
} as const;

const OFFLINE = "Check your internet connection, then try again.";

/** Why locked files did not open: the server's own words, or the plain words for a lost connection. */
export function openFailure(caught: unknown): { message: string } {
  return { message: caught instanceof ApiError ? caught.message : OFFLINE };
}

/** Whole seconds left of the check: the server's `seconds` when it was read, less the time
 * since by performance.now() (never this PC's clock, which may be set wrong), never below 0. */
export function secondsLeft(seconds: number, readAt: number, now: number): number {
  return Math.max(0, Math.ceil(seconds - Math.max(0, now - readAt) / 1000));
}

/** "Open for 9:05": the countdown beside the pages. */
export function openForText(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `Open for ${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

/** "Page 2 of 5". */
export const pageOfText = (index: number, total: number) => `Page ${index + 1} of ${total}`;

/** A page picture with its turn for everyone (PRD 7 section 14), sized as PageViewer.tsx sizes a
 * turned page: turned 90 or 270 it shows sideways, so the frame takes the turned aspect ratio
 * (`shownAspect`) and the picture, at its own proportions, is turned about its middle, never
 * clipped. In percentages of the frame, so it needs no measuring. */
export function turnedPicture(
  page: Pick<PageSummary, "width" | "height">,
  turn: number,
): { frame: CSSProperties; picture: CSSProperties } {
  const aspect = pageAspect(page);
  const sideways = turn === 90 || turn === 270;
  const shownAspect = sideways ? 1 / aspect : aspect; // the page as it shows, turned
  return {
    frame: { aspectRatio: String(shownAspect) },
    picture: {
      width: sideways ? `${100 * aspect}%` : "100%", // its own width: the frame's height
      height: sideways ? `${100 / aspect}%` : "100%", // its own height: the frame's width
      transform: `translate(-50%, -50%)${turn === 0 ? "" : ` rotate(${turn}deg)`}`,
    },
  };
}

// --- locking and unlocking (Admins) ---

/** The lock dialog's title: one file by its name, or a selection by its count. */
export const lockTitle = (names: readonly string[]) =>
  names.length === 1 ? `Lock "${names[0]}"?` : `Lock ${formatNumber(names.length)} files?`;

export function lockExplainText(count: number): string {
  const which = count === 1 ? "this file" : "these files";
  return (
    "Locked files are kept out of Ask, search inside files and totals, for everyone, including you. " +
    `People open them directly after a fresh company sign-in. Dashboard totals will no longer count ${which}.`
  );
}

export const LOCK_TEXT = {
  photograph: "People who can open it can still photograph the screen.",
  who: "Who can still open it:",
  loadingPeople: "Loading who can see it…",
  lock: (count: number) => (count === 1 ? "Lock file" : `Lock ${formatNumber(count)} files`),
  locking: "Locking…",
  cancel: "Cancel",
  menu: "Lock",
  menuHint: "Keep it out of Ask and open it only after a fresh sign-in",
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

/** A failed lock or unlock in words: the server's own (not lockable, in Trash). */
export const lockFailureText = (caught: unknown) => (caught instanceof ApiError ? caught.message : OFFLINE);
