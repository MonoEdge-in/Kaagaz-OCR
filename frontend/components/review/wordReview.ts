// Word-level review and Ensemble after the scan, the document screen's rules and words
// (scan review UX rules, Tushar, 16 Oct 2026, sections 2 to 7), kept pure so they are unit
// tested in node (tests/word-review.test.ts). Each action helper calls the API and gives
// back what to show: the new state, or the API's own message (409 changed_since: reload
// the word). Every sentence here follows section 6: calm, specific, sentence case, no
// exclamation marks, ending with an action or a finished state.

import {
  ApiError,
  confirmWord,
  editWord,
  keepAllEnsemble,
  keepReading,
  revertReading,
  startEnsemble,
  stopEnsemble,
  retryEnsemble,
  undoChange,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { Band, EnsembleRun, EnsembleScope, ReviewStatus, WordChange, WordFlag } from "@/lib/types";

// --- Bands (section 2): a glyph, an underline style and words, never colour alone ---

export const BANDS: Record<Band, { name: string; meaning: string }> = {
  check: { name: "Check", meaning: "a second look is wise" },
  review: { name: "Review", meaning: "unclear source, please confirm" },
};

/** "Flagged for review: 'Rs 4,850', a second look is wise, reason: smudged print". */
export function flagLabel(flag: Pick<WordFlag, "text" | "band" | "reason_text">): string {
  return `Flagged for review: '${flag.text}', ${BANDS[flag.band].meaning}, reason: ${flag.reason_text}`;
}

/** "Check · a second look is wise · smudged print": the review panel's band and reason. */
export const bandLine = (flag: Pick<WordFlag, "band" | "reason_text">) =>
  `${BANDS[flag.band].name} · ${BANDS[flag.band].meaning} · ${flag.reason_text}`;

/** The exact score, small and muted, only when something measured it. */
export const scoreText = (score: number | null) => (score === null ? null : `Score ${score.toFixed(2)}`);

export const IN_ENSEMBLE = "in Ensemble";

// --- Summary bar (section 3) and review progress (section 4) ---

export const EXIT_LABEL = "Exit review";
export const ENSEMBLE_FLAGGED = "Run Ensemble on flagged words";
export const ENSEMBLE_DOCUMENT = "Run Ensemble on full document";
export const SEND_TO_ENSEMBLE = "Send to Ensemble";
export const EXPORT_LABEL = "Export as Data"; // Vrushit UAT (25 Oct 2026): the read text as a .txt file
export const DOWNLOAD_LABEL = "Download original";
export const ENSEMBLE_INFO =
  "Ensemble reads the same text with several recognition models and compares their readings. When they agree, the word is resolved. When they don't, it stays flagged for you, with each reading shown.";
export const ABOUT_ENSEMBLE = "About Ensemble";

const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString("en-IN")} ${n === 1 ? one : many}`;

/** Reviewed by a person or resolved by Ensemble: both count as done. */
export const doneCount = (status: Pick<ReviewStatus, "reviewed" | "resolved">) => status.reviewed + status.resolved;

export const progressText = (done: number, total: number) =>
  `${done.toLocaleString("en-IN")} of ${total.toLocaleString("en-IN")} reviewed`;

export function finishedText(total: number): string {
  return total === 1
    ? "Verified. The flagged word is reviewed or resolved. Document ready to export."
    : `Verified. All ${total.toLocaleString("en-IN")} flagged words are reviewed or resolved. Document ready to export.`;
}

/**
 * The panel's status line (document panel UX, 25 Oct 2026): one true thing, from the counts
 * the review itself uses (`remaining`), so "Everything is checked" never sits beside a count
 * of words still to check. While it is read, the document's own status words.
 */
export function statusLine(status: ReviewStatus, scanning: string): { text: string; clear: boolean } {
  if (status.status !== "ready") return { text: scanning, clear: false };
  if (status.remaining > 0) {
    return { text: `${plural(status.remaining, "word")} ${status.remaining === 1 ? "needs" : "need"} a look`, clear: false };
  }
  return { text: status.flagged === 0 ? "No words need a look" : "Everything is checked", clear: true };
}

/** The status line's one button: what it does, for a reviewer (check) or a member (show). */
export const checkLabel = (status: Pick<ReviewStatus, "remaining" | "can_review">) =>
  `${status.can_review ? "Check" : "Show"} ${status.remaining === 1 ? "it" : "them"}`;

/** "More details" (section 7's trust signals, in plain words): pages, words, checked, last change. */
export function trustText(status: ReviewStatus): string {
  const parts = [plural(status.pages, "page"), plural(status.words, "word")];
  if (status.flagged > 0) {
    parts.push(`${doneCount(status).toLocaleString("en-IN")} of ${status.flagged.toLocaleString("en-IN")} checked`);
  }
  if (status.last_change_at) parts.push(`Last change ${formatDateTime(status.last_change_at)}`);
  return parts.join(" · ");
}

// --- Ensemble (section 5) ---

export const CONFIRM_OVER = 50; // more words than this asks first

/** Asked first: the full document, or more than 50 words; never for one word. */
export const needsConfirm = (scope: EnsembleScope, words: number) =>
  scope === "document" || (scope !== "word" && words > CONFIRM_OVER);

export const aboutText = (seconds: number) => `About ${Math.max(1, Math.round(seconds / 60))} min`;

/** The confirm sheet: "Run Ensemble on all 14 pages? About 3 min. You can keep reviewing while it runs." */
export function confirmText(scope: EnsembleScope, estimate: { pages: number; words: number; seconds: number }): string {
  const what =
    scope === "document"
      ? estimate.pages === 1
        ? "the 1 page"
        : `all ${plural(estimate.pages, "page")}`
      : plural(estimate.words, "flagged word");
  return `Run Ensemble on ${what}? ${aboutText(estimate.seconds)}. You can keep reviewing while it runs.`;
}

export const runActive = (run: EnsembleRun | null | undefined) => run?.status === "queued" || run?.status === "running";

/** "Ensemble · page 5 of 14" while it runs. */
export function runText(run: EnsembleRun): string {
  if (run.page_now === null) return "Ensemble · starting";
  return `Ensemble · page ${run.page_now.toLocaleString("en-IN")} of ${run.pages_total.toLocaleString("en-IN")}`;
}

/** "9 to 14" with an en dash, "3 and 5–7" (views.page_ranges). */
export function pageRanges(pages: readonly number[]): string {
  const runs: [number, number][] = [];
  for (const page of [...new Set(pages)].sort((a, b) => a - b)) {
    const last = runs[runs.length - 1];
    if (last && page === last[1] + 1) last[1] = page;
    else runs.push([page, page]);
  }
  const parts = runs.map(([first, last]) => (first === last ? `${first}` : `${first}–${last}`));
  return parts.length === 1 ? (parts[0] ?? "") : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** "Retry pages 9–14" or "Retry page 9". */
export const retryLabel = (pages: readonly number[]) =>
  `Retry ${new Set(pages).size === 1 ? "page" : "pages"} ${pageRanges(pages)}`;

/** "Ensemble resolved 9 of 12 flagged words. 3 still need your review." for a run that ended. */
export function resultText(run: EnsembleRun): string {
  const resolved = run.agreed + run.changed;
  const left = Math.max(0, run.words_total - resolved);
  const noun = run.scope === "document" ? "words" : "flagged words";
  const head = run.status === "stopped" ? "Ensemble stopped. The pages it finished are saved. " : "";
  const rest = left === 0 ? "" : ` ${left.toLocaleString("en-IN")} ${left === 1 ? "still needs" : "still need"} your review.`;
  return `${head}Ensemble resolved ${resolved.toLocaleString("en-IN")} of ${run.words_total.toLocaleString("en-IN")} ${noun}.${rest}`;
}

/** Changes Ensemble proposes that wait for Keep or Revert. */
export const openChanges = (run: EnsembleRun) => Math.max(0, run.changed - run.kept);

export const CHANGES_NOTE = "Review the changes.";
export const KEEP_ALL = "Keep all";

/** Every reading of an unsettled word, the text now first, each once. */
export const readingsOf = (flag: Pick<WordFlag, "text" | "alternatives">) => [
  ...new Set([flag.text, ...(flag.alternatives ?? [])]),
];

/** "Readings differ: 'Rs 4,850' / 'Rs 4,350'. Choose one or type the correct value." */
export const readingsDifferText = (readings: readonly string[]) =>
  `Readings differ: ${readings.map((text) => `'${text}'`).join(" / ")}. Choose one or type the correct value.`;

/** Ensemble read it and did not settle it: every reading is an option. */
export const unsettled = (flag: WordFlag) =>
  flag.state === "open" &&
  flag.ensemble !== null &&
  (flag.ensemble.outcome === "unresolved" || flag.ensemble.decision === "reverted");

/** A change Ensemble proposes, waiting for Keep or Revert. */
export const proposal = (flag: WordFlag) =>
  flag.state === "proposed" && flag.ensemble?.proposal ? { before: flag.text, after: flag.ensemble.proposal } : null;

// --- After a change (sections 4 and 7) ---

/** "Reviewed by you · 16 Oct 2026, 10:42 am", "Resolved by Ensemble · …", or null while open. */
export function doneText(flag: WordFlag): string | null {
  if (!flag.change || (flag.state !== "reviewed" && flag.state !== "resolved")) return null;
  const when = flag.change.at ? ` · ${formatDateTime(flag.change.at)}` : "";
  if (flag.state === "resolved") return `Resolved by Ensemble${when}`;
  return `${flag.change.by_you ? "Reviewed by you" : "Reviewed by a reviewer"}${when}`;
}

/** A row's short state in the flagged words list. */
export function stateText(flag: WordFlag): string {
  if (flag.state === "reviewed") return "Reviewed";
  if (flag.state === "resolved") return "Resolved by Ensemble";
  if (flag.state === "in_ensemble") return IN_ENSEMBLE;
  if (flag.state === "proposed") return "Change to keep or revert";
  return "Not reviewed yet";
}

/** Reviewed by a person or resolved by Ensemble: its mark on the page turns green (#431). */
export const settled = (flag: Pick<WordFlag, "state">) => flag.state === "reviewed" || flag.state === "resolved";

/**
 * Whether a change to `word` may have kept its page as is, or (its Undo) put it back (#443): no
 * other word on its page is open in `flags`, the words in `changed` (changed since `flags` came)
 * left out. Only then is the whole document asked for again after it, not only its words.
 */
export const lastOpenOnPage = (
  flags: readonly Pick<WordFlag, "word_key" | "page_no" | "state">[],
  word: Pick<WordFlag, "word_key" | "page_no">,
  changed: ReadonlySet<string> = new Set(),
) =>
  !flags.some(
    (other) =>
      other.page_no === word.page_no && other.word_key !== word.word_key && !changed.has(other.word_key) && !settled(other),
  );

/** A word's mark on the page, by name: "Reviewed: 'Wheat'" once settled, else its flag. */
export function markLabel(flag: WordFlag): string {
  if (settled(flag)) return `${stateText(flag)}: '${flag.text}'`;
  return `${flagLabel(flag)}${flag.state === "in_ensemble" ? `, ${IN_ENSEMBLE}` : ""}`;
}

/** History: "Reviewed by you", "Resolved by Ensemble", "Ensemble reading kept by Ravi Sharma". */
export function changeTitle(change: WordChange): string {
  const who = change.by_you ? "you" : (change.user_name ?? "a reviewer");
  if (change.action === "resolve") return "Resolved by Ensemble";
  if (change.action === "keep") return `Ensemble reading kept by ${who}`;
  return `Reviewed by ${who}`;
}

export const HISTORY_FILTERS = [
  { id: "all", label: "All changes" },
  { id: "person", label: "Reviewed by people" },
  { id: "ensemble", label: "Resolved by Ensemble" },
] as const;
export type HistoryFilter = (typeof HISTORY_FILTERS)[number]["id"];

// --- Export (section 7) ---

export const EXPORT_NOTE = "Saves the text as read, page by page, and the values found, as a plain text file (.txt).";
export const EXPORT_WAIT = "Export is available when scanning finishes.";
export const REVIEW_FIRST = "Review first";
export const EXPORT_ANYWAY = "Export anyway";

// --- Errors (section 6: what happened, what it means, what to do next) ---

export const NOT_SENT = "This did not reach Kaagaz. Nothing was changed. Check your internet connection, then try again.";

export const failureText = (caught: unknown) =>
  caught instanceof ApiError && caught.code !== "error" ? caught.message : NOT_SENT;

// --- Read this spot again (#385): only the word's spot of its page, cut out and zoomed ---

export const READ_SPOT_AGAIN = "Read this spot again";
export const READ_SPOT_SENT = "This spot is being read again. The page updates when it is done.";

/** The spot to read again: the word's box on its page (page fractions); null for a cell. */
export const spotOf = (flag: Pick<WordFlag, "region">) => (flag.region.kind === "box" ? flag.region.box : null);

// --- Keyboard (section 4): never while typing, never inside a dialog or a menu ---

export type ReviewKey = "next" | "previous" | "confirm" | "edit" | "ensemble" | "exit";

export interface KeyEventLike {
  key: string;
  shiftKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  /** The focused element: its tag, contenteditable, and whether it sits in a dialog or a menu. */
  target: { tagName: string; isContentEditable: boolean; inOverlay: boolean } | null;
}

const TYPING = new Set(["INPUT", "TEXTAREA", "SELECT"]);
const PRESSABLE = new Set(["BUTTON", "A", "SUMMARY"]);

/** N / Shift+N next or previous, Enter confirm, E edit, S send to Ensemble, Esc exit. */
export function reviewKey(event: KeyEventLike): ReviewKey | null {
  const target = event.target;
  if (event.ctrlKey || event.metaKey || event.altKey) return null;
  if (target && (TYPING.has(target.tagName) || target.isContentEditable || target.inOverlay)) return null;
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  if (key === "n") return event.shiftKey ? "previous" : "next";
  if (event.shiftKey) return null;
  if (key === "Enter") return target && PRESSABLE.has(target.tagName) ? null : "confirm"; // a focused button keeps its own Enter
  if (key === "e") return "edit";
  if (key === "s") return "ensemble";
  if (key === "Escape") return "exit";
  return null;
}

// --- Moving through the flags ---

/** The next flag still open after `from` (wrapping), else -1. */
export function nextOpen(flags: readonly WordFlag[], from: number): number {
  for (let step = 1; step <= flags.length; step += 1) {
    const at = (from + step) % flags.length;
    const flag = flags[at];
    if (flag && (flag.state === "open" || flag.state === "in_ensemble" || flag.state === "proposed")) return at;
  }
  return -1;
}

/** Shift+click: every key from the last one clicked to this one is set to `on`. */
export function selectRange(keys: readonly string[], selected: ReadonlySet<string>, from: string | null, to: string, on: boolean) {
  const next = new Set(selected);
  const a = from === null ? -1 : keys.indexOf(from);
  const b = keys.indexOf(to);
  const [start, end] = a < 0 ? [b, b] : [Math.min(a, b), Math.max(a, b)];
  for (const key of keys.slice(start, end + 1)) {
    if (on) next.add(key);
    else next.delete(key);
  }
  return next;
}

// --- Actions: the API call, then what to show ---

export type Done<T> = { ok: true; value: T; message: string } | { ok: false; message: string; reload: boolean };

async function attempt<T>(call: () => Promise<T>, said: (value: T) => string): Promise<Done<T>> {
  try {
    const value = await call();
    return { ok: true, value, message: said(value) };
  } catch (caught) {
    const reload = caught instanceof ApiError && (caught.code === "changed_since" || caught.code === "already_decided");
    return { ok: false, message: failureText(caught), reload };
  }
}

/** Confirm the word as it reads, or save `text` in its place; both name the text the person saw. */
export const saveWord = (documentId: string, flag: WordFlag, text: string | null) =>
  attempt(
    () =>
      text === null
        ? confirmWord(documentId, flag.word_key, flag.text)
        : editWord(documentId, flag.word_key, text, flag.text),
    (saved) => (text === null ? `Confirmed '${saved.text}'.` : `Saved '${saved.text}'.`),
  );

export const undoWord = (documentId: string, changeId: string) =>
  attempt(() => undoChange(documentId, changeId), (undone) => `Undone. The word reads '${undone.before_text}' again.`);

export const runEnsemble = (documentId: string, scope: EnsembleScope, keys: string[] = []) =>
  attempt(() => startEnsemble(documentId, scope, keys), (run) => `Ensemble started on ${plural(run.pages_total, "page")}.`);

export const stopRun = (documentId: string, runId: string) =>
  attempt(() => stopEnsemble(documentId, runId), () => "Ensemble stopped. The pages it finished are saved.");

export const retryRun = (documentId: string, runId: string) =>
  attempt(() => retryEnsemble(documentId, runId), (run) => `Ensemble started again on ${plural(run.pages_total, "page")}.`);

export const keepAll = (documentId: string, runId: string) =>
  attempt(() => keepAllEnsemble(documentId, runId), (kept) => `Kept ${plural(kept.kept, "change")}.`);

export const decide = (documentId: string, readingId: string, keep: boolean) =>
  attempt<unknown>(
    () => (keep ? keepReading(documentId, readingId) : revertReading(documentId, readingId)),
    () => (keep ? "Kept Ensemble's reading." : "Reverted. The word stays flagged for you."),
  );

/** The id of a flag's button on the page, where focus goes back when review ends. */
export const flagDomId = (key: string) => `flag-${key}`;
