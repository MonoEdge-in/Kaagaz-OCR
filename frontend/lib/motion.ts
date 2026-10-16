"use client";

// Motion from script (motion pass, 16 Oct 2026), for the moves CSS cannot start on its own:
// a tab's bar sliding from the tab chosen before, a zoom, a page or a new row fading in.
// The Web Animations API, time-based, the same curve as --ease in app/globals.css. Only
// transform and opacity can be asked for (the Frame type); nothing moves at all under
// reduced motion. No will-change: the browser promotes a running transform by itself.

import { useLayoutEffect, useRef } from "react";
import type { RefObject } from "react";

export const EASE = "cubic-bezier(0.2, 0, 0.2, 1)";
export const MOTION_MS = { fast: 120, med: 160, slow: 200 } as const;

export type Frame = { opacity?: number; transform?: string };

export function reducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Plays `frames` once on `element` (or on its ::after); a no-op without the API or under reduced motion. */
export function play(element: Element | null | undefined, frames: Frame[], ms: number, pseudoElement?: string) {
  if (!element || typeof element.animate !== "function" || reducedMotion()) return;
  element.animate(frames, { duration: ms, easing: EASE, pseudoElement });
}

export const FADE_IN: Frame[] = [{ opacity: 0 }, { opacity: 1 }];

/** The bar's move from the tab at `from` to the tab at `to`: where it was, then where it is. */
export function barFrames(from: { left: number; width: number }, to: { left: number; width: number }): Frame[] | null {
  if (to.width <= 0 || (from.left === to.left && from.width === to.width)) return null;
  return [{ transform: `translateX(${from.left - to.left}px) scaleX(${from.width / to.width})` }, { transform: "none" }];
}

/**
 * MonoEdge tabs: when `selected` changes, the chosen tab's 2px bar (its ::after, with
 * transform-origin left) slides and stretches from the tab chosen before, and the panel the
 * tab controls fades in. Tab widths never change on selection (their bold width is kept),
 * so the two rects read here are the ones on screen.
 */
export function useTabMotion(list: RefObject<HTMLElement | null>, selected: string) {
  const last = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    const tab = list.current?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]') ?? null;
    const before = last.current;
    last.current = tab;
    if (!tab || !before || before === tab || !before.isConnected) return;
    const frames = barFrames(before.getBoundingClientRect(), tab.getBoundingClientRect());
    if (frames) play(tab, frames, MOTION_MS.slow, "::after");
    const panel = tab.getAttribute("aria-controls");
    play(panel ? document.getElementById(panel) : null, FADE_IN, MOTION_MS.med);
  }, [list, selected]);
}
