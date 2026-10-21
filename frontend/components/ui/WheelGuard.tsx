"use client";

// A wheel turned over a focused number field or slider must scroll the page,
// never change the value (brief B). One passive listener for the whole app,
// mounted once in app/layout.tsx: it takes the focus off the field instead.

import { useEffect } from "react";

/** Blurs `active` when it is a number field or a slider; true when it did. */
export function blurIfNumberField(active: Element | null): boolean {
  if (active?.tagName !== "INPUT") return false;
  const input = active as HTMLInputElement;
  if (input.type !== "number" && input.type !== "range") return false;
  input.blur();
  return true;
}

export function WheelGuard() {
  useEffect(() => {
    const onWheel = () => blurIfNumberField(document.activeElement);
    document.addEventListener("wheel", onWheel, { passive: true });
    return () => document.removeEventListener("wheel", onWheel);
  }, []);
  return null;
}
