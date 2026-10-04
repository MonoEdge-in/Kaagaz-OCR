// The shared 0.5 s tick (lib/tick.ts) under fake timers.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TICK_MS, subscribeTick, tickNow } from "@/lib/tick";

const page = { visibilityState: "visible", onChange: null as (() => void) | null };
const fakeDocument = {
  get visibilityState() {
    return page.visibilityState;
  },
  addEventListener: (_: string, listener: () => void) => (page.onChange = listener),
  removeEventListener: () => (page.onChange = null),
};

beforeEach(() => {
  vi.useFakeTimers();
  page.visibilityState = "visible";
  vi.stubGlobal("document", fakeDocument);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("tick", () => {
  it("runs one timer for every listener, from the first to the last", () => {
    const counts = [0, 0, 0];
    const stops = counts.map((_, i) => subscribeTick(() => void (counts[i] = (counts[i] ?? 0) + 1)));
    counts.fill(0);
    expect(vi.getTimerCount()).toBe(1);
    vi.advanceTimersByTime(3 * TICK_MS);
    expect(counts).toEqual([3, 3, 3]);
    expect(tickNow()).toBe(tickNow()); // the same between ticks
    stops.slice(0, 2).forEach((stop) => stop());
    expect(vi.getTimerCount()).toBe(1);
    stops[2]?.();
    expect([vi.getTimerCount(), page.onChange]).toEqual([0, null]);
  });

  it("sleeps while the tab is hidden and ticks at once when it is shown", () => {
    let ticks = 0;
    const stop = subscribeTick(() => void (ticks += 1));
    page.visibilityState = "hidden";
    page.onChange?.();
    vi.advanceTimersByTime(10 * TICK_MS);
    expect([vi.getTimerCount(), ticks]).toEqual([0, 1]); // the one tick as it started
    page.visibilityState = "visible";
    page.onChange?.();
    expect(ticks).toBe(2);
    vi.advanceTimersByTime(TICK_MS);
    expect(ticks).toBe(3);
    stop();
  });
});
