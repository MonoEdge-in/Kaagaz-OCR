// One shared 500 ms clock for every countdown on the screen (PRD 4 #13): one
// timer however many rows count down. It starts with the first listener and
// stops with the last, sleeps while the tab is hidden, and ticks at once when
// the tab is shown again (tests/tick.test.ts).

import { useSyncExternalStore } from "react";

export const TICK_MS = 500;

const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;
let now = 0; // performance.now() at the last tick: the same value between ticks

const page = () => (typeof document === "undefined" ? null : document);

function tick() {
  now = performance.now();
  for (const listener of listeners) listener();
}

function onVisibility() {
  if (timer !== null) clearInterval(timer);
  timer = null;
  if (page()?.visibilityState === "hidden") return;
  tick();
  timer = setInterval(tick, TICK_MS);
}

/** Listen to the shared tick; returns the way to stop listening. */
export function subscribeTick(listener: () => void): () => void {
  listeners.add(listener);
  if (listeners.size === 1) {
    page()?.addEventListener("visibilitychange", onVisibility);
    onVisibility();
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size > 0) return;
    if (timer !== null) clearInterval(timer);
    timer = null;
    page()?.removeEventListener("visibilitychange", onVisibility);
  };
}

/** performance.now() at the last tick. */
export const tickNow = () => now;

/** The last tick's time, in performance.now() milliseconds (0 on the server). */
export function useTick(): number {
  return useSyncExternalStore(subscribeTick, tickNow, () => 0);
}
