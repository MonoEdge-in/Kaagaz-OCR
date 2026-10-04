// "Ready in about 40 s" next to a document being read (PRD 4 #13, council 4 Oct
// 2026; wording and thresholds PROVISIONAL). Pure: given performance.now() times.
//
// - Each answer counts down from when it arrived (markReceived), on the shared
//   tick (lib/tick.ts), so the server's and the browser's clocks never meet.
// - A lower estimate applies at once; a higher one within max(10 s, 20%) of what
//   is shown is absorbed; a bigger rise in the same step says "Taking longer than
//   usual" until the next answer, which then applies. A new step applies at once.
// - While reading, it never counts below the time after reading (the floor).
// - No move past slow_after_seconds: "Taking longer than usual". Low confidence:
//   "Estimating...", never a number it can't stand behind.
// - 60 s or more: whole minutes; 10 to 59 s: 5 s steps, rounded up; under 10 s:
//   "Almost done". Reduced motion: minutes only. Screen-reader words change only
//   by the minute.

import type { DocumentProgress, TimeLeft } from "./types";

export interface Held {
  value: TimeLeft; // the latest answer
  valueAt: number; // when it arrived
  step: DocumentProgress["step"];
  anchor: number; // when ``seconds`` were the time left
  seconds: number;
  rising: boolean; // a big rise in the same step: "taking longer" until the next answer
}

export type TimeLeftView = { state: "waiting" | "estimating" | "counting" | "almost" | "longer"; text: string; spoken: string };

const received = new WeakMap<TimeLeft, number>();

/** Note when each answer's time_left arrived. Called as the answer arrives. */
export function markReceived(items: readonly { time_left?: TimeLeft | null }[], at: number): void {
  for (const item of items) if (item.time_left) received.set(item.time_left, at);
}

/** When this time_left arrived; one never noted (a reply merged in) is noted the first time it is shown. */
export function receivedAt(value: TimeLeft): number {
  const at = received.get(value) ?? performance.now();
  received.set(value, at);
  return at;
}

/** What to count down from once a new answer arrives at ``at``. */
export function hold(previous: Held | null, value: TimeLeft, step: Held["step"], at: number): Held {
  const fresh: Held = { value, valueAt: at, step, anchor: at, seconds: value.seconds, rising: false };
  if (previous === null || previous.step !== step || previous.rising || stalled(previous, at)) return fresh;
  const shown = Math.max(0, previous.seconds - (at - previous.anchor) / 1000);
  if (value.seconds <= shown) return fresh;
  if (value.seconds - shown <= Math.max(10, 0.2 * shown)) return { ...previous, value, valueAt: at };
  return { ...fresh, rising: true };
}

function stalled(held: Held, now: number): boolean {
  const since = held.value.since_move_seconds + Math.max(0, now - held.valueAt) / 1000;
  return since > held.value.slow_after_seconds;
}

export const WAITING: TimeLeftView = { state: "waiting", text: "Waiting to start", spoken: "Waiting to start" };

/** What to show at ``now``. */
export function describe(held: Held, now: number, reducedMotion: boolean): TimeLeftView {
  if (held.rising || stalled(held, now)) {
    return { state: "longer", text: "Taking longer than usual", spoken: "Taking longer than usual" };
  }
  if (held.value.confidence === "low") {
    return { state: "estimating", text: "Estimating\u2026", spoken: "Estimating the time left" };
  }
  let left = Math.max(0, held.seconds - Math.max(0, now - held.anchor) / 1000);
  if (held.step === "reading") left = Math.max(left, held.value.after_seconds);
  if (left < 10) return { state: "almost", text: "Almost done", spoken: "Almost done" };
  const minutes = Math.max(1, Math.round(left / 60));
  const spoken = left < 60 ? "Ready in less than a minute" : `Ready in about ${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
  const step = Math.ceil(left / 5) * 5;
  const text = left >= 60 || reducedMotion || step >= 60 ? `Ready in about ${minutes} min` : `Ready in about ${step} s`;
  return { state: "counting", text, spoken };
}
