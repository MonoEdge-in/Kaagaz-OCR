"use client";

// A small tooltip (Tushar, 16 Oct 2026: the confidence on a label, on hover). It shows a
// moment after the pointer rests on its anchor or the anchor gets keyboard focus, hides
// on leave, blur, scroll or Esc, and is placed so it never runs past the screen's edges.
// The anchor is focusable only because it has a tip, and the tip describes it.

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

export const TIP_DELAY = 300; // ms before it shows
const LEAVE_DELAY = 120; // ms, so the pointer can cross to the tip itself
const EDGE = 8; // px kept clear of the screen's edges
const GAP = 4; // px between the anchor and the tip

interface Box {
  top: number;
  bottom: number;
  left: number;
}

/** Under the anchor, above it when there is no room below; never past an edge. */
export function placeTip(
  anchor: Box,
  tip: { width: number; height: number },
  view: { width: number; height: number },
): { top: number; left: number } {
  const below = anchor.bottom + GAP;
  const top =
    below + tip.height + EDGE <= view.height ? below : Math.max(EDGE, anchor.top - GAP - tip.height);
  const left = Math.max(EDGE, Math.min(anchor.left, view.width - EDGE - tip.width));
  return { top, left };
}

/** The key that hides an open tip. */
export const hidesTip = (key: string) => key === "Escape";

export function Tip({ text, className, children }: { text: string; className?: string; children: ReactNode }) {
  const id = useId();
  const anchor = useRef<HTMLSpanElement>(null);
  const tip = useRef<HTMLSpanElement>(null);
  const timer = useRef<number | undefined>(undefined);
  const [open, setOpen] = useState(false);

  const later = (next: boolean, ms: number) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setOpen(next), ms);
  };
  const hide = () => {
    window.clearTimeout(timer.current);
    setOpen(false);
  };

  useLayoutEffect(() => {
    const at = anchor.current;
    const box = tip.current;
    if (!open || !at || !box) return;
    const spot = placeTip(at.getBoundingClientRect(), box.getBoundingClientRect(), {
      width: document.documentElement.clientWidth,
      height: window.innerHeight,
    });
    box.style.top = `${spot.top}px`;
    box.style.left = `${spot.left}px`;
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    const onKey = (event: KeyboardEvent) => {
      if (hidesTip(event.key)) close();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", close, { capture: true, passive: true });
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", close, true);
    };
  }, [open]);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  // Open, it sits on the body: a card with a transform would otherwise hold a fixed box.
  const box = (
    <span ref={tip} id={id} role="tooltip" className="tip tabular" hidden={!open}>
      {text}
    </span>
  );
  return (
    <span
      className="tip-wrap"
      onMouseEnter={() => later(true, open ? 0 : TIP_DELAY)}
      onMouseLeave={() => later(false, LEAVE_DELAY)}
    >
      <span
        ref={anchor}
        className={className}
        tabIndex={0}
        aria-describedby={id}
        onFocus={() => later(true, TIP_DELAY)}
        onBlur={hide}
        onKeyDown={(event) => {
          if (hidesTip(event.key)) hide();
        }}
      >
        {children}
      </span>
      {open ? createPortal(box, document.body) : box}
    </span>
  );
}
