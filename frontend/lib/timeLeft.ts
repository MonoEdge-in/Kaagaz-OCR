// "Ready in about 40 s" next to a document being read (PRD 4 #13, councils of 4 Oct
// 2026; wording and thresholds PROVISIONAL). Pure: given performance.now() times.
//
// - Each answer counts down from when it arrived (markReceived), on the shared
//   tick (lib/tick.ts), so the server's and the browser's clocks never meet.
// - A lower estimate applies at once; a higher one within max(10 s, 20%) of what
//   is shown is absorbed; a bigger rise in the same step says "Taking longer than
//   usual" until the next answer, which then applies. A new step applies at once.
// - While preparing or reading, it never counts below the time after that step's
//   pages (the floor: after_seconds).
// - No move past slow_after_seconds: "Taking longer than usual". Low confidence:
//   the number is labelled a rough guess, "About 3 min (rough guess)".
//   "Estimating..." only when there is no estimate at all; "Starting..." while the
//   file is scanned.
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

export type TimeLeftView = {
  state: "waiting" | "starting" | "estimating" | "counting" | "rough" | "almost" | "longer";
  text: string;
  spoken: string;
};

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
export const STARTING: TimeLeftView = { state: "starting", text: "Starting\u2026", spoken: "Starting" };
export const ESTIMATING: TimeLeftView = { state: "estimating", text: "Estimating\u2026", spoken: "Estimating the time left" };

/** The seconds left at ``now``, counted down from the held answer, never below the floor. */
export function secondsLeft(held: Held, now: number): number {
  const left = Math.max(0, held.seconds - Math.max(0, now - held.anchor) / 1000);
  const floored = held.step === "preparing" || held.step === "reading";
  return floored ? Math.max(left, held.value.after_seconds) : left;
}

/** What to show at ``now``. */
export function describe(held: Held, now: number, reducedMotion: boolean): TimeLeftView {
  if (held.rising || stalled(held, now)) {
    return { state: "longer", text: "Taking longer than usual", spoken: "Taking longer than usual" };
  }
  const left = secondsLeft(held, now);
  if (left < 10) return { state: "almost", text: "Almost done", spoken: "Almost done" };
  const minutes = Math.max(1, Math.round(left / 60));
  const when = left < 60 ? "in less than a minute" : `in about ${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
  const step = Math.ceil(left / 5) * 5;
  const amount = left >= 60 || reducedMotion || step >= 60 ? `${minutes} min` : `${step} s`;
  if (held.value.confidence === "low") {
    return { state: "rough", text: `About ${amount} (rough guess)`, spoken: `Rough guess: ready ${when}` };
  }
  return { state: "counting", text: `Ready in about ${amount}`, spoken: `Ready ${when}` };
}
