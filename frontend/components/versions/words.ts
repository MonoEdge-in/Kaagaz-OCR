// Versions (File management V5 to V7, V9 to V12): every word of the new-version banner, the
// upload tray's line, the Versions tab and the Compare view, in one place so the wording is
// easy to change, and the pure functions behind them (tests/file-management.test.ts).

import { formatNumber } from "@/lib/format";
import type { DocumentDetail, DocumentVersion, VersionSuggestion } from "@/lib/types";

export const VERSION_TEXT = {
  // V6, V7: the banner.
  makeIt: (n: number) => `Make it version ${n}`,
  keepSeparate: "Keep separate",
  compareFirst: "Compare first",
  undo: "Undo",
  noteLabel: "Note for this version (optional)",
  notePlaceholder: "e.g. Mutation 1234 added",
  linked: (n: number, name: string) => `Linked as version ${n} of ${name}.`,
  linkedOnItsOwn: (n: number, name: string) => `Kaagaz linked this as version ${n} of ${name}, as it is almost the same.`,
  kept: "Kept as its own document. Kaagaz will not suggest this pair again.",
  undone: "Undone. This is its own document again.",
  notNewest: "A later version was uploaded since, so this link stays.",
  working: "Saving…",
  // V5: the upload tray.
  review: "Review",
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
  // V11: the Compare view.
  compareTitle: "Compare versions",
  nextChange: "Next change",
  noChanges: "The text is the same in both.",
  removed: "Removed",
  added: "Added",
  loading: "Comparing…",
  loadFailed: "The comparison did not load.",
  back: "Back to the document",
  page: (n: number) => `Page ${n}`,
  before: "The version compared with",
  after: "This version",
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

const joinAnd = (parts: readonly string[]) =>
  parts.length <= 1 ? parts.join("") : `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`;

// The lines of kaagaz/versions.py (TEXT_ALONE, NAME_SIGNAL), for a reply without the server's words.
const TEXT_LINE = 0.8;
const NAME_LINE = 0.6;

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
    const percent = Math.round(text * 100);
    return `${scores.text_overlap_estimated ? "about " : ""}${percent === 100 && text < 1 ? 99 : percent}% the same text`;
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

/** Make current (V10) is offered for a ready version that is not current, out of Trash. */
export function canMakeCurrent(version: Pick<DocumentVersion, "status" | "is_current">, inTrash: boolean): boolean {
  return !inTrash && !version.is_current && version.status === "ready";
}

/** A note as saved: spaces collapsed, null when empty; a problem when it is too long. */
export function cleanNote(note: string): { note: string | null; problem: string | null } {
  const clean = note.split(/\s+/).filter(Boolean).join(" ");
  if (clean.length > NOTE_MAX) return { note: clean, problem: VERSION_TEXT.noteTooLong };
  return { note: clean === "" ? null : clean, problem: null };
}
