"use client";

// Small browser hooks for the chat screen. Each reads the browser through
// useSyncExternalStore or an observer callback, never by setting state
// straight from an effect.

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import type { RefObject } from "react";

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

/**
 * True once the element has come within 200 px of view, inside the nearest
 * ancestor marked data-scroll-root (the chat thread) or else the viewport.
 * It stays true after that.
 */
export function useNearView(ref: RefObject<Element | null>): boolean {
  const [near, setNear] = useState(() => typeof IntersectionObserver === "undefined");
  useEffect(() => {
    const element = ref.current;
    if (!element || near) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setNear(true);
          observer.disconnect();
        }
      },
      { root: element.closest("[data-scroll-root]"), rootMargin: "200px 0px" },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, near]);
  return near;
}

/** "smooth", unless the person asked the system for reduced motion. */
export function scrollBehavior(): ScrollBehavior {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}
