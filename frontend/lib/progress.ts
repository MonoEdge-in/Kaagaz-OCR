// How far a document being read has got, as a percentage (PRD 4 #13 fix, council of
// 4 Oct 2026; the 5% steps and the 99% cap PROVISIONAL). Pure: given performance.now()
// times.
//
// - fraction = elapsed / (elapsed + left): the time since this run started over the
//   whole time it is expected to take, so no step needs a weight of its own. ``left``
//   is what the countdown shows (lib/timeLeft.ts secondsLeft), which already counts
//   down within a page between answers.
// - It never goes back: the highest percent so far is kept for this attempt. A new
//   attempt (``attempts`` changes) starts again from 0.
// - At most 99% until the document is ready (and then the bar is gone).
// - Held still while it is taking longer than usual: the time left is not known then.
// - The <progress> moves in 5% steps; the text beside it shows the whole percent.

import { secondsLeft } from "./timeLeft";
import type { Held } from "./timeLeft";

export const CAP_PERCENT = 99;
export const BAR_STEP_PERCENT = 5;

/** What the bar shows: the highest whole percent reached in this attempt. */
export interface ShownProgress {
  attempt: number;
  percent: number;
}

/** elapsed / (elapsed + left), 0 when neither is known. */
export function fractionDone(elapsedSeconds: number, leftSeconds: number): number {
  if (!Number.isFinite(elapsedSeconds) || !Number.isFinite(leftSeconds)) return 0;
  const elapsed = Math.max(0, elapsedSeconds);
  const total = elapsed + Math.max(0, leftSeconds);
  return total > 0 ? elapsed / total : 0;
}

/** The fraction done at ``now``: the run's elapsed time counts up from when the answer arrived. */
export function fractionAt(held: Held, now: number): number {
  const elapsed = held.value.elapsed_seconds + Math.max(0, now - held.valueAt) / 1000;
  return fractionDone(elapsed, secondsLeft(held, now));
}

/**
 * The next thing to show: the same object when nothing changes (so a screen can skip
 * an update). ``fraction`` null: no estimate yet, so it stays where it is.
 */
export function advance(
  previous: ShownProgress | null,
  attempt: number,
  fraction: number | null,
  holding: boolean,
): ShownProgress {
  const same = previous !== null && previous.attempt === attempt ? previous : { attempt, percent: 0 };
  if (fraction === null || holding || !Number.isFinite(fraction)) return same;
  const percent = Math.min(CAP_PERCENT, Math.max(0, Math.floor(fraction * 100)));
  return percent > same.percent ? { attempt, percent } : same;
}

/** The <progress> value: the percent in whole 5% steps, never more than it shows. */
export function barValue(percent: number): number {
  return Math.floor(percent / BAR_STEP_PERCENT) * BAR_STEP_PERCENT;
}
