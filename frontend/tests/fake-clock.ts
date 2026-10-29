// The fake clock for an upload under vi.useFakeTimers (#447), shared by the upload tests.

import { vi } from "vitest";

/**
 * Let real work and the fake clock move on until `isDone`, for at most `realMs` of real time:
 * one setImmediate turn, then `step` ms of fake time only while a timer is pending. Each file's
 * hash (crypto.subtle) takes real time, and on a loaded machine a fixed number of turns, or of
 * fake milliseconds, ran out before it did, so the upload then waited on a timer that never fired.
 */
export async function settle(isDone: () => boolean, realMs = 4_000, step = 100): Promise<void> {
  for (const until = performance.now() + realMs; !isDone() && performance.now() < until; ) {
    await new Promise((resolve) => setImmediate(resolve));
    if (vi.getTimerCount() > 0) vi.advanceTimersByTime(step);
  }
}
