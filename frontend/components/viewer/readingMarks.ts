// Reading problems drawn on the page (PRD 5 U3), kept pure so they are unit tested in
// node (tests/reading-marks.test.ts). Three marks, each with its own pattern and words,
// so colour is never the only cue: an unsure word is dashed amber, a part that couldn't
// be read ([?]) has a double red edge and a light fill, a spot not confirmed is dotted grey. Each mark is a
// button named for what it marks; its hit area is at least 24 x 24 px (WCAG 2.5.8),
// and the Attention panel lists the same marks. The text is shown as read (visibleText).
// #431: a mark whose flagged words a person fixed or confirmed turns green (markChecked).

import { settled } from "@/components/review/wordReview";
import { markerRect } from "@/components/words/spots";
import type { PixelRect } from "@/lib/regions";
import type { PageSummary, ReadingMark, WordFlag } from "@/lib/types";
import { visibleText } from "./scanned";
import { sameRegion } from "./selection";

export type MarkQuality = ReadingMark["quality"];

export const MIN_HIT_PX = 24;

export const MARK_WORDS: Record<MarkQuality, { name: string; legend: string }> = {
  unsure: { name: "Unsure word", legend: "Unsure word (dashed)" },
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
