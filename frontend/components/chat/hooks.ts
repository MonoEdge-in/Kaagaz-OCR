"use client";

// Small browser hooks for the chat screen. Each reads the browser through
// useSyncExternalStore or an observer callback, never by setting state
// straight from an effect.

import { useCallback, useSyncExternalStore } from "react";

/** True while the media query matches; false on the server. */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false,
  );
}

const MINUTE_MS = 60_000;

function subscribeMinute(onChange: () => void) {
  const timer = window.setInterval(onChange, MINUTE_MS);
  return () => window.clearInterval(timer);
}

// Rounded to the minute, so the value stays the same between ticks.
const currentMinute = () => Math.floor(Date.now() / MINUTE_MS) * MINUTE_MS;

/** The time now, in milliseconds, updated every minute (0 on the server). */
export function useMinuteNow(): number {
  return useSyncExternalStore(subscribeMinute, currentMinute, () => 0);
}

/** "smooth", unless the person asked the system for reduced motion. */
export function scrollBehavior(): ScrollBehavior {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}
