"use client";

// The shell's status bar (brief C): a fixed 32 px row under <main> where a
// screen shows background work, such as a scan or an Ensemble check, instead
// of a modal. AppShell provides the row; a screen puts its line there with
// <InStatusBar>. The row is always laid out, so a line arriving moves nothing.

import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

export const StatusBarSlot = createContext<HTMLElement | null>(null);

export function InStatusBar({ children }: { children: ReactNode }) {
  const slot = useContext(StatusBarSlot);
  return slot ? createPortal(children, slot) : null;
}
