// Reading problems drawn on the page (PRD 5 U3), kept pure so they are unit tested in
// node (tests/reading-marks.test.ts). Three marks, each with its own pattern and words,
// so colour is never the only cue: an unsure word is dashed amber, a part that couldn't
// be read ([?]) has a double red edge and a light fill, a spot not confirmed is dotted grey. Each mark is a
// button named for what it marks; its hit area is at least 24 x 24 px (WCAG 2.5.8),
// and the Attention panel lists the same marks. The text is shown as read (visibleText).
// #431: a mark whose flagged words a person fixed or confirmed turns green (markChecked).
// #523: marks point, never hide the text. An unsure word is a thin dashed underline; the word
// marks (unsure, couldn't read) follow "Show words to check" (on at first for reviewers and
// admins), a page with more than WORD_MARK_CAP of them shows a chip instead, and a word mark far
// taller than a word (a strip the height of a paragraph, stored before the fix) is never drawn.

import { settled } from "@/components/review/wordReview";
import { markerRect } from "@/components/words/spots";
import type { PixelRect } from "@/lib/regions";
import type { PageSummary, ReadingMark, Region, Role, WordFlag } from "@/lib/types";
import { visibleText } from "./scanned";
import { sameRegion } from "./selection";

export type MarkQuality = ReadingMark["quality"];

export const MIN_HIT_PX = 24;

export const MARK_WORDS: Record<MarkQuality, { name: string; legend: string }> = {
  unsure: { name: "Unsure word", legend: "Unsure word (dashed underline)" },
  illegible: { name: "Hard to read", legend: "Hard to read (double edge)" },
  unpinned: { name: "Spot not confirmed", legend: "Spot not confirmed (dotted)" },
};

const ORDER: readonly MarkQuality[] = ["unsure", "illegible", "unpinned"];

/** The page's marks that can be drawn: boxes on this page, in the server's order. */
export function marksOnPage(marks: readonly ReadingMark[] | undefined, pageNo: number): ReadingMark[] {
  return (marks ?? []).filter((mark) => mark.region.kind === "box" && mark.region.page === pageNo);
}

/** "Unsure word 'xyz', mark 2 of 5" (", checked" once done): the button's name, and the Attention panel's words. */
export function markName(mark: ReadingMark, index: number, total: number, checked = false): string {
  const text = visibleText(mark.text).trim();
  const words = MARK_WORDS[mark.quality].name;
  return `${text ? `${words} '${text}'` : words}, mark ${index + 1} of ${total}${checked ? ", checked" : ""}`;
}

/** True when the words flagged at this mark's spot are all reviewed or resolved (#431); a word
 * fixed and then undone is open again, so its mark is not. */
export function markChecked(mark: ReadingMark, flags: readonly Pick<WordFlag, "region" | "state">[]): boolean {
  const here = flags.filter((flag) => sameRegion(flag.region, mark.region));
  return here.length > 0 && here.every(settled);
}

/** The Needs attention tab's short chip (UI audit DD7): the word as read, else what the mark is. */
export function markChipText(mark: ReadingMark): string {
  return visibleText(mark.text).trim() || MARK_WORDS[mark.quality].name;
}

/** The legend: one entry for each kind of mark the page has, in a fixed order. */
export function legend(marks: readonly ReadingMark[]): MarkQuality[] {
  return ORDER.filter((quality) => marks.some((mark) => mark.quality === quality));
}

/** "Show reading problems" starts on for an uncertain or unreadable page, off for a good one. */
export function showByDefault(page: Pick<PageSummary, "read_quality">): boolean {
  return page.read_quality !== "good";
}

/** Where a mark goes on a page drawn at width x height (markerRect: never tiny, kept on the page). */
export function markRect(mark: ReadingMark, width: number, height: number): PixelRect | null {
  return mark.region.kind === "box" ? markerRect(mark.region.box, width, height) : null;
}

/** #523: more word marks than this on a page are not drawn; a chip says how many instead. */
export const WORD_MARK_CAP = 40;
/** #523: a word mark is drawn only up to this many times the page's usual word height ... */
export const TALL_TIMES = 2.5;
/** ... or this share of the page's height, whichever is larger. */
export const TALL_SHARE = 0.06;

/** A mark on one word (PRD 5 F3): unsure, or couldn't read. A spot not confirmed is a line's. */
export function isWordMark(mark: Pick<ReadingMark, "quality">): boolean {
  return mark.quality === "unsure" || mark.quality === "illegible";
}

/** "Show words to check" starts on for a reviewer or an admin, off for a member (#523). A viewer
 * told no role (a crop, a test) keeps the marks it drew before. */
export function showWordsByDefault(role: Role | undefined): boolean {
  return role !== "member";
}

export function wordsChipText(count: number): string {
  return `${count} words to check on this page`;
}

function heightOf(region: Region): number | null {
  return region.kind === "box" ? region.box[3] - region.box[1] : null;
}

/**
 * The tallest a word mark on this page is drawn, as a share of the page's height (#523): TALL_TIMES
 * the median height of its word marks, or TALL_SHARE, whichever is larger. The median is taken over
 * the marks no taller than TALL_SHARE, so a page whose marks are all stored strips never raises its
 * own limit. O(n log n).
 */
export function tallLimit(marks: readonly ReadingMark[]): number {
  const heights = marks
    .filter(isWordMark)
    .map((mark) => heightOf(mark.region))
    .filter((height): height is number => height !== null && height <= TALL_SHARE)
    .sort((a, b) => a - b);
  const half = Math.floor(heights.length / 2);
  const median = heights.length % 2 ? heights[half]! : ((heights[half - 1] ?? 0) + (heights[half] ?? 0)) / 2;
  return Math.max(TALL_TIMES * median, TALL_SHARE);
}

export interface WordMarkView {
  /** The page's word marks, drawn or not: all of them are still listed. */
  total: number;
  /** Over WORD_MARK_CAP while shown and not opened: none drawn, a chip instead. */
  capped: boolean;
  /** Whether a word mark (or a word flag) at this region is drawn. */
  drawn: (region: Region) => boolean;
}

/** Which of a page's word marks are drawn (#523): none while "Show words to check" is off (`on`),
 * none over WORD_MARK_CAP until the chip is pressed (`opened`), and never one over tallLimit. */
export function wordMarkView(marks: readonly ReadingMark[], on: boolean, opened: boolean): WordMarkView {
  const words = marks.filter(isWordMark);
  const limit = tallLimit(words);
  const capped = on && !opened && words.length > WORD_MARK_CAP;
  return {
    total: words.length,
    capped,
    drawn: (region) => on && !capped && (heightOf(region) ?? 0) <= limit,
  };
}
