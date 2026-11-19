// Versions (File management V5 to V7, V9 to V12): every word of the new-version banner, the
// upload tray's line, the Versions tab and the Compare view, in one place so the wording is
// easy to change, and the pure functions behind them (tests/file-management.test.ts).

import { formatNumber } from "@/lib/format";
import type { DocumentDetail, DocumentVersion, VersionSuggestion } from "@/lib/types";

export const VERSION_TEXT = {
  // V6, V7: the banner, the upload tray's row and the compare screen (#521: two buttons).
  asVersion: "Upload as a new version",
  asFile: "Upload as a new file",
  seeDifferences: "See the differences",
  undo: "Undo",
  noteLabel: "Note for this version (optional)",
  notePlaceholder: "e.g. Mutation 1234 added",
  linked: (n: number, name: string) => `Saved as version ${n} of "${name}".`,
  linkedOnItsOwn: (n: number, name: string) => `Kaagaz linked this as version ${n} of ${name}, as it is almost the same.`,
  kept: "Kept as its own file. Kaagaz won't ask about these two again.",
  undone: "Undone. This is its own document again.",
  reverted: "Undone.", // #521: the toast's Undo after a choice or Make it a separate file
  notNewest: "A later version was uploaded since, so this link stays.",
  working: "Saving…",
  // V5: the upload tray.
  review: "Review",
  // PRD 9 F-06 (D4): the pop-up once a file just uploaded looks like a new version.
  popupTitle: "A new version?",
  saveAsVersion: "Save it as a new version?",
  yes: "Yes",
  no: "No",
  yesToAll: "Yes to all",
  close: "Close",
  // V12: the Versions tab.
  versions: "Versions",
  current: "Current",
  view: "View",
  compare: "Compare",
  makeCurrent: "Make current",
  madeCurrent: (n: number) => `Version ${n} is now the one search and Ask use.`,
  joinedUploaded: "Uploaded",
  joinedSuggested: "Linked from a suggestion",
  joinedAuto: "Linked on its own",
  note: "Note",
  addNote: "Add a note",
  editNote: "Edit note",
  noNote: "No note",
  noteSaved: "Note saved.",
  noteTooLong: "Write a note of at most 200 characters.",
  onlyOne: "This is the only version.",
  // #521: Make it a separate file.
  separate: "Make it a separate file",
  separateTitle: (n: number) => `Make version ${n} a separate file`,
  separateNote: (family: string) =>
    `It keeps its reading, words you fixed, and history. Kaagaz won't suggest it as a version of "${family}" again.`,
  separateButton: "Make separate file",
  separated: (n: number, name: string) => `Version ${n} is now its own file, "${name}".`,
  nameLabel: "Name",
  // V11: the Compare view.
  compareTitle: "Compare versions",
  nextChange: "Jump to next change",
  noChanges: "The text is the same in both.",
  removed: "Removed",
  added: "Added",
  loading: "Comparing…",
  loadFailed: "The comparison did not load.",
  back: "Back to the document",
  page: (n: number) => `Page ${n}`,
  noPage: (n: number) => `No page ${n} in this file`, // #521 review: one side is shorter
  before: "The version compared with",
  after: "This version",
  yours: (name: string) => `The file you have, "${name}"`, // #521: a suggested pair's left side
  upload: (name: string) => `The new upload, "${name}"`,
  truncated: "This page is long, so only its first part is compared.",
};

/** The longest note a version takes (V9). */
export const NOTE_MAX = 200;

/** The upload tray's line (V5): "1 file looks like a new version"; null for none. */
export function suggestionsText(count: number): string | null {
  if (count <= 0) return null;
  return count === 1 ? "1 file looks like a new version" : `${formatNumber(count)} files look like new versions`;
}

const IDENTIFIER_WORDS: Record<string, string> = {
  survey_number: "survey number",
  khata_number: "khata number",
  invoice_number: "invoice number",
  agreement_number: "agreement number",
  account_number: "account number",
  gstin: "GSTIN",
  pan: "PAN",
};

/** "agreement number" from "agreement_number". */
export function identifierWords(kind: string): string {
  return IDENTIFIER_WORDS[kind] ?? kind.replaceAll("_", " ");
}

type Scores = Pick<VersionSuggestion, "text_overlap" | "name_similarity" | "shared_identifier"> &
  Partial<Pick<VersionSuggestion, "reasons" | "text_overlap_estimated">>;

export const joinAnd = (parts: readonly string[]) =>
  parts.length <= 1 ? parts.join("") : `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`;

// The lines of kaagaz/versions.py (TEXT_ALONE, NAME_SIGNAL), for a reply without the server's words.
const TEXT_LINE = 0.8;
const NAME_LINE = 0.6;
// #521: the name this alike or more highlights Upload as a new version, else Upload as a new file.
const SAME_NAME = 0.8;

/**
 * The whole percent below (86.9% is 86), at most 99, and 100 only when counted exactly the same,
 * never from the estimate (kaagaz/versions.py share_percent). The overlap was a 4-byte real, so
 * it is first rounded to 4 places of the percent (0.83 reads 0.8299999833: 83).
 */
export function sharePercent(overlap: number, estimated: boolean): number {
  const percent = Math.floor(Math.round(overlap * 100 * 10_000) / 10_000);
  if (percent >= 100) return overlap >= 1 && !estimated ? 100 : 99;
  return percent;
}

/** #521: Upload as a new version is the highlighted choice for the same name, else Upload as a new file. */
export function highlightVersion(suggestion: Pick<VersionSuggestion, "name_similarity">): boolean {
  return (suggestion.name_similarity ?? 0) >= SAME_NAME;
}

/**
 * Why Kaagaz thinks so, in plain words (V6, NB-17, #521): "86% the same text" ("about" when only
 * estimated) when the text led; else "same name and same survey number". The server's own
 * words when it sends them; "" when nothing counted.
 */
export function reasonsText(scores: Scores): string {
  if (typeof scores.reasons === "string" && scores.reasons.trim() !== "") return scores.reasons.trim();
  if (Array.isArray(scores.reasons) && scores.reasons.length > 0) return joinAnd(scores.reasons);
  const text = scores.text_overlap;
  if (text !== null && text >= TEXT_LINE) {
    const estimated = scores.text_overlap_estimated === true;
    return `${estimated ? "about " : ""}${sharePercent(text, estimated)}% the same text`;
  }
  const reasons: string[] = [];
  const name = scores.name_similarity;
  if (name !== null && name >= NAME_LINE) reasons.push(name >= 1 ? "same name" : "similar name");
  if (scores.shared_identifier) reasons.push(`same ${identifierWords(scores.shared_identifier)}`);
  return joinAnd(reasons);
}

/** 'This looks like a new version of "7/12 Survey 44 Wakad.pdf" (86% the same text).' */
export function bannerTitle(candidateName: string, reason: string): string {
  return `This looks like a new version of "${candidateName}"${reason ? ` (${reason})` : ""}.`;
}

export type BannerState =
  | { kind: "ask"; suggestion: VersionSuggestion } // pending: the three buttons
  | { kind: "linked"; suggestion: VersionSuggestion; canUndo: boolean } // accepted or automatic
  | { kind: "none" };

/**
 * What the banner on a document shows (V6, V7): the question while a suggestion about it is
 * pending; after a link (by a person, or on its own), the link with Undo while this document
 * is still the family's newest version; nothing for a kept-separate or undone pair. O(V).
 */
export function bannerState(detail: Pick<DocumentDetail, "id" | "version_suggestion" | "versions">): BannerState {
  const suggestion = detail.version_suggestion;
  if (!suggestion || suggestion.role !== "new" || suggestion.document.id !== detail.id) return { kind: "none" };
  if (suggestion.status === "pending" && suggestion.outcome === "suggested") return { kind: "ask", suggestion };
  if (suggestion.status === "accepted" || (suggestion.status === "pending" && suggestion.outcome === "auto")) {
    return { kind: "linked", suggestion, canUndo: isNewest(detail.id, detail.versions) };
  }
  return { kind: "none" };
}

/** This version is the family's newest (V7: Undo only then). */
export function isNewest(id: string, versions: readonly Pick<DocumentVersion, "id" | "version_no">[]): boolean {
  const newest = versions.reduce<Pick<DocumentVersion, "id" | "version_no"> | null>(
    (best, version) => (best === null || version.version_no > best.version_no ? version : best),
    null,
  );
  return newest === null || newest.id === id;
}

/** How a version joined its family, in words (V12). */
export function joinedText(joined: DocumentVersion["joined"]): string {
  if (joined === "linked") return VERSION_TEXT.joinedSuggested;
  if (joined === "auto") return VERSION_TEXT.joinedAuto;
  return VERSION_TEXT.joinedUploaded;
}

/** Make current (V10) is offered for a ready version that is not current, out of Trash, while a
 * version the person sees is current (`currentSeen`, PRD 9 F-07: one out of their sight can't be
 * made not current, so the server says 404). */
export function canMakeCurrent(
  version: Pick<DocumentVersion, "status" | "is_current">,
  inTrash: boolean,
  currentSeen: boolean,
): boolean {
  return !inTrash && currentSeen && !version.is_current && version.status === "ready";
}

/** A note as saved: spaces collapsed, null when empty; a problem when it is too long. */
export function cleanNote(note: string): { note: string | null; problem: string | null } {
  const clean = note.split(/\s+/).filter(Boolean).join(" ");
  if (clean.length > NOTE_MAX) return { note: clean, problem: VERSION_TEXT.noteTooLong };
  return { note: clean === "" ? null : clean, problem: null };
}

/**
 * #521, Make it a separate file: the name it is offered. Its own upload name when that differs
 * from the family's; else the family's with "(version n)" before the extension.
 */
export function separateDefaultName(ownName: string, familyName: string, versionNo: number): string {
  if (ownName.trim() !== "" && ownName !== familyName) return ownName;
  const dot = familyName.lastIndexOf(".");
  const [stem, extension] = dot > 0 ? [familyName.slice(0, dot), familyName.slice(dot)] : [familyName, ""];
  return `${stem} (version ${versionNo})${extension}`;
}

// The server's rule for a typed file name (api/names.py upload_name): no control, format (bidi,
// zero-width), private-use or unassigned character, nor a line or paragraph separator; ZWNJ and
// ZWJ stay (Devanagari needs them).
const HIDDEN = /[\p{Cc}\p{Co}\p{Cn}\p{Zl}\p{Zp}]|(?![\u200c\u200d])\p{Cf}/u;
const NAME_MAX = 255;

/** Why the name can't be used, or null: empty, hidden characters, or too long. */
export function separateNameProblem(name: string): string | null {
  const trimmed = name.trim();
  if (trimmed === "") return "Enter a name.";
  if (HIDDEN.test(name)) return "Remove the hidden characters from the name.";
  if (trimmed.length > NAME_MAX) return `Use at most ${NAME_MAX} characters.`;
  return null;
}
