// The decisions behind the admin screen, kept out of the components so they
// are unit tested in node (tests/shell-admin.test.ts). The server checks every
// one of these again; these checks only give a quicker, clearer message.

import { formatNumber } from "@/lib/format";
import type { DocumentStatus, Role, Stats } from "@/lib/types";

export const TABS = [
  { id: "users", label: "Users" },
  { id: "settings", label: "Settings" },
  { id: "activity", label: "Activity" },
  { id: "numbers", label: "Numbers" },
] as const;

export type TabId = (typeof TABS)[number]["id"];

/** Arrow keys, Home and End move between tabs (the WAI-ARIA tabs pattern). */
export function tabAfterKey(index: number, key: string, count: number): number | null {
  switch (key) {
    case "ArrowRight":
      return (index + 1) % count;
    case "ArrowLeft":
      return (index - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}

export const ROLES: readonly Role[] = ["member", "reviewer", "admin"];

export const MIN_PASSWORD_LENGTH = 12;

export interface NewUserInput {
  email: string;
  display_name: string;
  role: Role;
  password: string;
}

export type NewUserErrors = Partial<Record<"email" | "display_name" | "password", string>>;

/** Messages per field; an empty object means the form may be sent. */
export function validateNewUser(input: NewUserInput): NewUserErrors {
  const errors: NewUserErrors = {};
  const email = input.email.trim();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) errors.email = "Enter a valid e-mail address.";
  if (input.display_name.trim() === "") errors.display_name = "Enter a name.";
  if (input.password.length < MIN_PASSWORD_LENGTH) {
    errors.password = `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  return errors;
}

export type UploadLimit = { ok: true; value: number | null } | { ok: false; message: string };

/**
 * The upload limit box: empty means "use the platform limit" (null), otherwise
 * a whole number of MB from 1 up to the platform limit.
 */
export function parseUploadLimit(text: string, platformMaxMb: number): UploadLimit {
  const trimmed = text.trim();
  if (trimmed === "") return { ok: true, value: null };
  if (!/^\d+$/.test(trimmed)) return { ok: false, message: "Enter a whole number of MB." };
  const value = Number(trimmed);
  if (value < 1) return { ok: false, message: "The limit must be at least 1 MB." };
  if (value > platformMaxMb) {
    return { ok: false, message: `The limit can be at most ${platformMaxMb} MB.` };
  }
  return { ok: true, value };
}

export interface Tile {
  label: string;
  value: string;
}

const STATUS_WORDS: Record<DocumentStatus, string> = {
  received: "Documents waiting for the virus check",
  clean: "Documents being read",
  ready: "Documents ready",
  needs_reader: "Documents stored, not readable yet",
  rejected: "Documents rejected",
  failed: "Documents that failed",
};

const STATUS_ORDER: readonly DocumentStatus[] = [
  "ready",
  "received",
  "clean",
  "needs_reader",
  "rejected",
  "failed",
];

/** The Numbers tab, in a fixed order, with Indian digit grouping. */
export function statTiles(stats: Stats): Tile[] {
  const documents = STATUS_ORDER.reduce(
    (sum, status) => sum + (stats.documents_by_status[status] ?? 0),
    0,
  );
  const tiles: Tile[] = [{ label: "Documents", value: formatNumber(documents) }];
  for (const status of STATUS_ORDER) {
    tiles.push({
      label: STATUS_WORDS[status],
      value: formatNumber(stats.documents_by_status[status] ?? 0),
    });
  }
  tiles.push(
    { label: "Questions asked", value: formatNumber(stats.questions_total) },
    { label: "Answered", value: formatNumber(stats.answered) },
    { label: "Not found in the documents", value: formatNumber(stats.not_found) },
    { label: "Answers labelled Verified", value: formatNumber(stats.verified_answers) },
    { label: "Answers labelled Needs review", value: formatNumber(stats.needs_review_answers) },
    { label: "Thumbs up", value: formatNumber(stats.feedback_up) },
    { label: "Thumbs down", value: formatNumber(stats.feedback_down) },
    { label: "Values waiting for review", value: formatNumber(stats.open_review_items) },
  );
  return tiles;
}

/**
 * An audit row's details as one line of plain text: "count: 3, status: ready".
 * The server keeps only ids, statuses and counts there; nested values are shown
 * as compact JSON text, never as markup.
 */
export function describeDetails(details: Record<string, unknown>): string {
  return Object.keys(details)
    .sort()
    .map((key) => {
      const value = details[key];
      const text =
        typeof value === "string" || typeof value === "number" || typeof value === "boolean"
          ? String(value)
          : value === null
            ? "none"
            : JSON.stringify(value);
      return `${key}: ${text}`;
    })
    .join(", ");
}

const ACTION_WORDS: Record<string, string> = {
  "auth.login": "Signed in",
  "auth.logout": "Signed out",
  "document.upload": "Uploaded a document",
  "document.delete": "Deleted a document",
  "chat.ask": "Asked a question",
  "review.resolve": "Corrected a value",
  "user.create": "Added a user",
  "user.update": "Changed a user",
  "settings.update": "Changed settings",
};

/** The audit action in words; an action this screen does not know is shown as is. */
export function describeAction(action: string): string {
  return ACTION_WORDS[action] ?? action;
}

export const AUDIT_PAGE_SIZE = 50;
