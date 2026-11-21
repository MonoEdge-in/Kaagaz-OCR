// Plain words for every action on a file (File management A4): one table, the experience
// page's phrases, said after who did it ("Priya viewed", "Anita changed the label to
// Restricted"). The log keeps ids only; the server fills the names in when it is shown
// (ActivityEntry). Pure functions, unit tested in node (tests/file-management.test.ts).

import { describeAction, describeTarget, whoText } from "@/components/admin/logic";
import { labelName } from "@/components/files/labels";
import { formatNumber } from "@/lib/format";
import type { ActivityEntry } from "@/lib/types";

type Entry = Pick<ActivityEntry, "action" | "actor_name" | "details"> &
  Partial<Pick<ActivityEntry, "document" | "folder" | "actor_id" | "subject_id" | "subject_name">>;

const text = (value: unknown) => (typeof value === "string" && value.trim() !== "" ? value : null);
const version = (entry: Entry, otherwise: number) => entry.document?.version_no ?? otherwise;
/** Where it went: the details' resolved path, else the row's folder as it is now. */
const place = (entry: Entry) => text(entry.details.folder_path) ?? entry.folder?.path ?? null;
/** PRD 7 (#601): whom an access change is about; "themselves" when they made it. */
const mine = (entry: Entry) => entry.subject_id != null && entry.subject_id === entry.actor_id;
const whom = (entry: Entry) => (mine(entry) ? "themselves" : (entry.subject_name ?? "someone"));
/** The file, as the server names it ("a file you can't see" for Management), else the folder. */
const where = (entry: Entry) => activityDocument(entry) ?? entry.folder?.path ?? "a folder";

/** What was done, keyed by the audit action. Each one reads after a person's name. */
export const ACTIVITY_WORDS: Record<string, (entry: Entry) => string> = {
  "document.view": () => "viewed",
  "document.download": () => "downloaded",
  "document.upload": () => "uploaded",
  "document.version": (entry) => `uploaded version ${version(entry, 2)}`,
  "version.accepted": (entry) => `linked version ${version(entry, 2)} (suggested by Kaagaz)`,
  "version.suggested": (entry) =>
    entry.details.outcome === "auto" ? `linked version ${version(entry, 2)} on its own` : "suggested it as a new version",
  "version.rejected": () => "kept it separate",
  "version.undone": () => "undid the version link",
  "version.make_current": (entry) => `made version ${version(entry, 1)} current`,
  "document.version_separated": (entry) =>
    `made version ${typeof entry.details.version_no === "number" ? entry.details.version_no : "…"} a separate file`,
  // #648: possible versions decided by Admins and Management; shown in the admin log only.
  "version.kept_apart": () => "marked it as not versions",
  "version.kept_apart_undone": () => "undid not versions",
  "version.later": () => "left it for later",
  // #648 (V-A): files linked as versions by Admins and Management, and its Undo; the admin log only.
  "version.linked": (entry) =>
    typeof entry.details.version_no === "number" ? `linked it as version ${entry.details.version_no}` : "linked it as a version",
  "version.unlinked": () => "undid linking the versions",
  "document.rename": (entry) => `renamed to ${text(entry.details.to_name) ?? entry.document?.name ?? "…"}`,
  "document.move": (entry) => `moved to ${place(entry) ?? "Not in a folder"}`,
  "document.label_change": (entry) => `changed the label to ${labelName(text(entry.details.to) ?? "…")}`,
  "document.owner_change": (entry) => `owner changed to ${text(entry.details.to_name) ?? "someone else"}`,
  "document.trash": () => "moved to Trash",
  "document.restore": () => "restored",
  "document.tags": () => "changed the tags",
  "document.autofile": (entry) => `filed it in ${place(entry) ?? "a folder"}`,
  "document.autofile_undo": () => "undid the automatic filing",
  "document.reprocess": () => "asked to read it again",
  "document.stop": () => "stopped reading it", // #713
  // #600: pages is how many pages the save changed, turned how many are left turned after it.
  "document.page_turns": ({ details: { pages, turned } }) => {
    if (turned === 0) return "put the pages back as scanned";
    if (typeof pages !== "number") return "changed the turn of the pages for everyone";
    return `changed the turn of ${formatNumber(pages)} ${pages === 1 ? "page" : "pages"} for everyone`;
  },
  // PRD 7 section 6 (PR 13): a file's own lock; opening locked files shows in the admin log only.
  "lock.lock": () => "locked it",
  "lock.unlock": () => "unlocked it",
  "audit.export": () => "exported the activity",
  "folder.upload": (entry) =>
    typeof entry.details.files === "number" ? `uploaded a folder of ${formatNumber(entry.details.files)} files` : "uploaded a folder",
  // PRD 7 (#601): who can see what ("Ravi added Priya to Finance").
  "access.management_on": (entry) => `made ${whom(entry)} Management`,
  "access.management_off": (entry) => `took Management away from ${whom(entry)}`,
  "access.member_add": (entry) => `added ${whom(entry)} to ${where(entry)}${entry.details.head === true ? " as head" : ""}`,
  "access.member_remove": (entry) => `removed ${whom(entry)} from ${where(entry)}`,
  "access.head_on": (entry) => `made ${whom(entry)} head of ${where(entry)}`,
  "access.head_off": (entry) => `removed ${whom(entry)} as head of ${where(entry)}`,
  "access.department_only_on": (entry) => `kept ${where(entry)} to its people`,
  "access.department_only_off": (entry) => `opened ${where(entry)} to everyone`,
  "access.private_on": (entry) => `made ${where(entry)} private`,
  "access.private_off": (entry) => `made ${where(entry)} open again`,
  "access.named_add": (entry) => `named ${whom(entry)} on ${where(entry)}`,
  "access.named_remove": (entry) => `took ${whom(entry)} off ${where(entry)}`,
  "access.file_give": (entry) => `gave ${whom(entry)} access to ${where(entry)}`,
  "access.file_take": (entry) =>
    `removed ${mine(entry) ? "their own" : `${entry.subject_name ?? "someone"}'s`} access to ${where(entry)}`,
  // Written by 0090's users_switched trigger when an account is turned off or on.
  "access.switched_off": (entry) =>
    `turned off ${entry.subject_name ?? "someone"}'s account; their access was removed`,
  "access.switched_on": (entry) =>
    `turned on ${entry.subject_name ?? "someone"}'s account; their old access was cleared`,
};

/** PRD 7 (#601): someone gave themselves access (the chip's words). The audit says `self` for an
 * Admin's sign-in-proved grant and for a Head naming themselves (10a), so the words name no role. */
export const SELF_GIVEN = "Gave themselves access";
/** The Admin tab's block of those entries, first. */
export const SELF_GIVEN_HEADING = "People who gave themselves access";
export const selfGiven = (entry: Pick<ActivityEntry, "details">) => entry.details.self === true;

/** The action in words; one this table does not know falls back to the admin log's words. */
export function activityWords(entry: Entry): string {
  const words = ACTIVITY_WORDS[entry.action];
  if (words) return words(entry);
  const said = describeAction(entry.action, entry.details);
  return said.charAt(0).toLowerCase() + said.slice(1);
}

/** Who did it: their name, "Kaagaz" for what Kaagaz did by itself, else "Someone". */
export function activityWho(entry: Entry): string {
  if (entry.actor_name === null && entry.action === "version.suggested") return "Kaagaz";
  const who = whoText(entry);
  return who === "—" ? "Someone" : who;
}

/** "Priya viewed". */
export function activitySentence(entry: Entry): string {
  return `${activityWho(entry)} ${activityWords(entry)}`;
}

/** The document a row is about; null when it is not about one, or for a delete, whose words
 * already say "deleted a document" (#693); a deleted one says so. */
export function activityDocument(entry: Entry): string | null {
  if (!entry.document || entry.action === "document.delete") return null;
  return entry.document.deleted || entry.document.name === null ? "a deleted document" : entry.document.name;
}

/**
 * The admin Activity tab's Document column (issue 375): the document; for an account event
 * the person it was about when that is not who did it ("Sign-in failed": Priya), else
 * nothing; for anything else, what it was done on.
 */
export function activityTarget(
  entry: Entry & Pick<ActivityEntry, "target_type" | "target_id"> & Partial<Pick<ActivityEntry, "subject_name">>,
): string {
  const document = activityDocument(entry);
  if (document !== null) return document;
  if (entry.target_type !== "user") return describeTarget(entry.target_type);
  return (entry.target_id !== entry.actor_id && entry.subject_name) || "—";
}

/** The action filter's choices (A5), in the order people look for them. */
export const ACTION_CHOICES: readonly { action: string; label: string }[] = [
  { action: "document.view", label: "Viewed" },
  { action: "document.download", label: "Downloaded" },
  { action: "document.upload", label: "Uploaded" },
  { action: "document.version", label: "New version" },
  { action: "version.accepted", label: "Linked a version" },
  { action: "document.rename", label: "Renamed" },
  { action: "document.move", label: "Moved" },
  { action: "document.label_change", label: "Label changed" },
  { action: "document.owner_change", label: "Owner changed" },
  { action: "document.trash", label: "Moved to Trash" },
  { action: "document.restore", label: "Restored" },
];

/** The words around the timelines and the admin Activity tab. */
export const ACTIVITY_TEXT = {
  heading: "Activity",
  empty: "Nothing has happened here yet.",
  loading: "Loading the activity…",
  loadFailed: "The activity did not load.",
  more: "Show more",
  tryAgain: "Try again",
  export: "Export to Excel",
  clearAll: "Clear all",
  person: "Person",
  about: "About a person", // PRD 7 (#601): whom an access change is about
  folder: "Folder",
  file: "File",
  action: "Action",
  date: "Date",
  anyone: "Anyone",
  anyFolder: "Any folder",
  anyAction: "Any action",
  fileSearch: "Find a file by name",
  noFiles: "No file has that name.",
  columns: { when: "When", who: "Who", action: "Action", document: "Document", folder: "Folder", details: "Details" },
};

/** PRD 7 (#601): Management's Access activity page (/access-activity). The 403 is the server's words. */
export const ACCESS_ACTIVITY_TEXT = {
  heading: "Access activity",
  hint: "Changes to who can see which files, newest first.",
  label: "Access changes, newest first",
  empty: "Nobody has changed who can see what yet.",
};
